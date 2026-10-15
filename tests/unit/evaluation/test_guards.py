from corki.evaluation import EvaluationDecision, evaluate_model_step
from corki.protocol.ids import new_turn_id
from corki.protocol.items import AssistantMessageItem, new_step_id


def test_valid_empty_model_completion_can_finalize() -> None:
    message = AssistantMessageItem("", new_turn_id(), new_step_id())

    result = evaluate_model_step(
        (message,),
    )

    assert result.decision is EvaluationDecision.FINALIZE
    assert result.error is None
