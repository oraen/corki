"""Ordinary question/answer data; no approval or credential semantics."""

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class UserInputOption:
    label: str
    description: str


@dataclass(frozen=True, slots=True)
class UserInputQuestion:
    id: str
    header: str
    question: str
    options: tuple[UserInputOption, ...]
    is_other: bool = True
    is_secret: bool = False


def parse_questions(arguments: object) -> tuple[UserInputQuestion, ...]:
    if not isinstance(arguments, Mapping) or not isinstance(arguments.get("questions"), list):
        raise ValueError("request_user_input requires a questions array")
    questions = []
    for question in arguments["questions"]:
        if not isinstance(question, Mapping):
            raise ValueError("each question must be an object")
        for key in ("id", "header", "question"):
            if not isinstance(question.get(key), str):
                raise ValueError(f"question {key} must be a string")
        options = question.get("options")
        if not isinstance(options, list) or not options:
            raise ValueError("request_user_input requires non-empty options for every question")
        parsed_options = []
        for option in options:
            if not isinstance(option, Mapping) or not all(
                isinstance(option.get(key), str) for key in ("label", "description")
            ):
                raise ValueError("question options require string label and description")
            parsed_options.append(UserInputOption(option["label"], option["description"]))
        if any(type(question.get(key, False)) is not bool for key in ("isOther", "isSecret")):
            raise ValueError("question flags must be booleans")
        questions.append(
            UserInputQuestion(
                question["id"],
                question["header"],
                question["question"],
                tuple(parsed_options),
                is_secret=question.get("isSecret", False),
            )
        )
    return tuple(questions)


def copy_response(response: object) -> dict[str, dict[str, dict[str, list[str]]]]:
    if not isinstance(response, Mapping) or not isinstance(response.get("answers"), Mapping):
        raise ValueError("user input response requires an answers object")
    answers = {}
    for key, value in response["answers"].items():
        if not isinstance(key, str) or not isinstance(value, Mapping):
            raise ValueError("user input answers must map string IDs to answer objects")
        items = value.get("answers")
        if not isinstance(items, list) or not all(isinstance(item, str) for item in items):
            raise ValueError("each user input answer requires an array of strings")
        answers[key] = {"answers": list(items)}
    return {"answers": answers}
