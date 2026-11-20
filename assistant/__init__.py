"""AQACT's offline assistant package.

Public API
----------
``answer_question``      answer free-text questions about the active dataset
``dataset_insights``     prioritised insight cards for the dashboard
``describe_plot``        plain/technical chart interpretation and caption
``explain_analysis``     explain extreme/compliance/ML/model-eval/comparative results
``describe_chart_image`` describe an uploaded chart image without any network call
``suggestions_for``      contextual starter questions
"""

from assistant.engine import answer_question, suggestions_for  # noqa: F401
from assistant.insights import dataset_insights, describe_plot, explain_analysis  # noqa: F401
from assistant.vision import describe_chart_image  # noqa: F401

__all__ = [
    "answer_question",
    "dataset_insights",
    "describe_plot",
    "explain_analysis",
    "describe_chart_image",
    "suggestions_for",
]
