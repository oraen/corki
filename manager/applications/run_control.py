"""Transport-neutral cancellation and interaction replies for applications."""

from .models import AppError
from .run_view import run_view


def cancel_run(apps, app, run_id):
    view = run_view(apps, app, run_id)
    apps.mutate(dict(action="stop", app=app["id"], session=view["session_id"], run=run_id))
    return run_view(apps, app, run_id)


def respond_run(apps, app, data):
    view = run_view(apps, app, data.get("run_id"))
    if data.get("decision") not in ("accept", "decline"):
        raise AppError("decision 必须是 accept 或 decline")
    response = data.get("response")
    if (view.get("interaction") or {}).get("kind") == "question":
        if data["decision"] != "accept":
            raise AppError("补充信息请使用 accept；取消任务请调用取消接口")
        if not isinstance(response, dict) or not response:
            raise AppError("response 必须是问题 ID 到回答的映射")
        answers = {}
        questions = {q["id"] for q in view["interaction"]["questions"]}
        for question, value in response.items():
            if isinstance(value, str):
                value = [value]
            if (
                question not in questions
                or not isinstance(value, list)
                or not all(isinstance(v, str) for v in value)
            ):
                raise AppError("response 中的问题 ID 或回答无效")
            answers[question] = {"answers": value}
        response = {"answers": answers}
    apps.mutate(
        dict(
            action="respond",
            app=app["id"],
            session=view["session_id"],
            run=view["run_id"],
            request=data.get("interaction_id"),
            decision=data["decision"],
            response=response,
        )
    )
    return run_view(apps, app, view["run_id"])
