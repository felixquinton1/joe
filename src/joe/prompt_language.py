"""One place decides the language the user reads.

Les consignes de comportement envoyées aux fournisseurs sont rédigées en
anglais ; seules les phrases que l'utilisateur lira vraiment suivent la langue
choisie dans Joe Web. Le mélange précédent — une organisation de réponse
rédigée en français, un exemple de question en français, un « nous » cité dans
un prompt anglais — pesait plus lourd que la consigne de langue : une interface
réglée sur l'anglais recevait des réponses en français.
"""

from __future__ import annotations

SUPPORTED = ("fr", "en")
DEFAULT_LANGUAGE = "fr"


def normalize(language: str | None) -> str:
    """Accept what the interface sends, including `en-US`, and never fail."""
    value = str(language or "").strip().lower()
    return "en" if value.startswith("en") else DEFAULT_LANGUAGE


# Ces intertitres appartiennent à la réponse : l'utilisateur les lit, donc ils
# suivent sa langue. Les citer en français dans un prompt anglais suffisait à
# faire basculer toute la réponse.
PLAN_HEADING = {"fr": "Ce que je vais faire :", "en": "What I will do:"}
RESULT_HEADING = {"fr": "Résultat :", "en": "Result:"}

_COLLECTIVE_PRONOUN = {"fr": "nous", "en": "we"}

_QUESTION_EXAMPLE = {
    "fr": '{"question": "Par quoi commencer ?", "options": ["Option courte", "Autre option"]}',
    "en": '{"question": "Where should we start?", "options": ["Short option", "Other option"]}',
}


def response_language(language: str) -> str:
    """The explicit language instruction, meant to be the last thing read."""
    if normalize(language) == "en":
        return (
            "\n\n# Response language\n"
            "Answer the user in English, including headings, section titles, and "
            "any closing question. Keep code, commands, paths, and quoted source "
            "text unchanged. This instruction overrides the language of the "
            "project files, of the conversation history, and of any example "
            "above; it applies to plans, reviews, consensus stages, and the "
            "final synthesis."
        )
    return (
        "\n\n# Langue de réponse\n"
        "Réponds à l'utilisateur en français, y compris les titres, les "
        "intertitres et la question finale éventuelle. Conserve le code, les "
        "commandes, les chemins et les citations de sources dans leur forme "
        "d'origine. Cette consigne prime sur la langue des fichiers du projet, "
        "sur celle de l'historique et sur celle des exemples ci-dessus ; elle "
        "s'applique aux plans, revues, étapes de consensus et à la synthèse "
        "finale."
    )


def response_organization(language: str) -> str:
    """Separate progress announcements from the final answer."""
    code = normalize(language)
    return (
        "# Response organization\n"
        "Clearly separate progress announcements from the final answer.\n"
        "\n"
        "- Before acting, briefly state what you are about to do under "
        f"`{PLAN_HEADING[code]}`.\n"
        "- Keep intermediate updates to useful progress information.\n"
        "- Once the work is finished, start the final answer with "
        f"`{RESULT_HEADING[code]}`.\n"
        "- Make the final answer self-contained and centred on the result. Do "
        "not repeat the initial plan, and do not mix future intentions with "
        "completed work.\n"
        "- Write those two headings, and everything else the user reads, in the "
        "requested response language.\n"
    )


def collective_voice(language: str) -> str:
    """Ask for a collective voice by naming the pronoun of the right language."""
    pronoun = _COLLECTIVE_PRONOUN[normalize(language)]
    return (
        "Use a factual collective voice — the response language's first-person "
        f"plural, '{pronoun}' — or impersonal phrasing, never an ambiguous "
        "first-person singular. "
    )


def question_rules(language: str) -> str:
    """Closing question convention, with an example in the response language.

    Le modèle n'a aucun canal interactif : il peut terminer son tour sur une
    question fermée que Joe rend cliquable, et le clic repart comme message
    suivant. Cette consigne n'a de sens que pour l'étape qui parle réellement à
    l'utilisateur. Portée par le contexte commun, elle atteignait aussi les
    propositions et relectures d'un consensus : chacune finissait sur une
    question que personne ne pouvait cliquer, et qui polluait le matériau
    transmis à la synthèse.
    """
    return f"""

# Ask the user for a decision
When a product trade-off, priority, or ambiguous choice genuinely belongs to
the user, end the response with a `joe:question` fenced block in exactly this
shape (2 to 4 short options, written in the response language):

```joe:question
{_QUESTION_EXAMPLE[normalize(language)]}
```

Joe renders it as buttons and sends the selected option as the next message.
Use it only when the answer materially changes the next step, never to request
execution permission.
"""
