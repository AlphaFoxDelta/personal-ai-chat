"""Subagents for the personal AI chat app.

A subagent is a background worker: one model call with its own system
prompt, focused on a slice of a bigger task. run_subagents() fans a task
out to several subagents in parallel threads, collects their answers, and
optionally asks the main model to merge everything into one reply.

Everything runs against the configured backend, so on Ollama it is free.
Parallel subagents share your machine, so each one runs a bit slower than
it would alone. Three subagents is a good default; more than five gets
sluggish on most machines.
"""

import concurrent.futures

from backends import BackendError


def ask(backend, system_prompt, task, web_context=""):
    """Run one subagent to completion and return its full reply text."""
    user_content = task
    if web_context:
        user_content = (
            "Use these web search results as background where relevant:\n\n"
            "%s\n\nTask: %s" % (web_context, task)
        )
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]
    return "".join(backend.stream(messages)).strip()


def run_subagents(backend, task, agents, system_prompt, synthesize=True,
                  max_workers=3, web_context=""):
    """Fan a task out to subagents in parallel and return their results.

    agents is a list of {"name": ..., "system_prompt": ...} dicts.
    system_prompt is the main assistant persona, used for the merge step.
    web_context is shared web search background handed to every subagent.

    Returns {"results": [...], "synthesis": str or None}. Each result is
    {"name": ..., "output": ...} or {"name": ..., "error": ...} so one
    failing subagent does not sink the rest.
    """
    task = (task or "").strip()
    if not task:
        raise BackendError("A task is required.")
    if not agents:
        raise BackendError("No subagents were given.")

    def _work(agent):
        name = agent.get("name") or "subagent"
        prompt = agent.get("system_prompt") or ""
        try:
            return {"name": name,
                    "output": ask(backend, prompt, task, web_context)}
        except BackendError as exc:
            return {"name": name, "error": str(exc)}
        except Exception:
            return {"name": name,
                    "error": "Subagent '%s' failed unexpectedly." % name}

    workers = max(1, min(max_workers, len(agents)))
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_work, agent) for agent in agents]
        results = [future.result() for future in futures]

    synthesis = None
    if synthesize:
        combined = "\n\n".join(
            "## %s\n%s" % (r["name"], r.get("output") or r.get("error", ""))
            for r in results
        )
        synthesis_messages = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": (
                    "You gave %d subagents this task:\n\n%s\n\n"
                    "Task: %s\n\n"
                    "Merge their answers into one clear, complete response. "
                    "Resolve disagreements sensibly. "
                    "Do not mention subagents in your reply."
                    % (len(results), combined, task)
                ),
            },
        ]
        synthesis = "".join(backend.stream(synthesis_messages)).strip()

    return {"results": results, "synthesis": synthesis}
