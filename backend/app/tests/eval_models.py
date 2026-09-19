"""Compare candidate models on the work this product actually does.

Not a benchmark run. Public leaderboards measure chat quality, coding and general
reasoning; none of those is what breaks here. What breaks here is:

1. **Schema compliance.** Every generator asks for a Pydantic model instance. A model that
   needs three repair rounds to produce valid JSON is three times slower and sometimes
   fails outright, and that is invisible on any leaderboard.
2. **Factual accuracy on answer keys.** A live run marked "chloroplast stroma" as the site
   of the light-dependent reactions. A wrong key is the most harmful output this product
   has, so accuracy on checkable facts matters more than eloquence.
3. **Counting.** "Write exactly 3 questions" has to produce three. The blueprint depends
   on it; under-production leaves topics unassessed.
4. **Speed on this GPU**, with the model fully resident.

Run it directly, not under pytest — it needs a real Ollama and takes minutes per model:

    python -m app.tests.eval_models                      # every installed candidate
    python -m app.tests.eval_models qwen3:8b ...         # named models
    python -m app.tests.eval_models --repeat 3 a b       # three passes, for a real verdict
"""

from __future__ import annotations

import asyncio
import statistics
import sys
import time
from dataclasses import dataclass, field

from pydantic import BaseModel, ConfigDict, Field

from app.config import get_settings
from app.llm.base import LLMProvider, system, user
from app.llm.ollama import OllamaProvider
from app.llm.structured import generate_structured
from app.schemas.paper import Question

# --------------------------------------------------------------------------- fixtures


class QuestionBatch(BaseModel):
    """The real schema the exam generator uses, with its cross-field validators."""

    model_config = ConfigDict(extra="ignore")

    questions: list[Question] = Field(default_factory=list)


@dataclass
class FactCase:
    """A question whose correct answer is unambiguous and checkable by substring."""

    topic: str
    right: tuple[str, ...]
    wrong: tuple[str, ...]


#: Deliberately mainstream secondary-school science and maths — if a model gets these
#: wrong, it cannot be trusted to write an answer key at all.
FACTS: list[FactCase] = [
    FactCase(
        "the site of the light-dependent reactions of photosynthesis",
        ("thylakoid", "grana"),
        ("stroma", "nucleus", "cytosol", "mitochondri"),
    ),
    FactCase(
        "which organelle carries out aerobic respiration in animal cells",
        ("mitochondri",),
        ("chloroplast", "nucleus", "ribosome", "golgi"),
    ),
    FactCase(
        "the enzyme that fixes carbon dioxide in the Calvin cycle",
        ("rubisco", "ribulose"),
        ("atp synthase", "amylase", "catalase", "polymerase"),
    ),
    FactCase(
        "the gas released as a by-product of the light reactions",
        ("oxygen", "o2", "o₂"),
        ("carbon dioxide", "co2", "nitrogen", "methane"),
    ),
    FactCase(
        "the powerhouse molecule cells use for immediate energy transfer",
        ("atp", "adenosine triphosphate"),
        ("dna", "glucose", "nadph", "rna"),
    ),
    FactCase(
        "the charge on an electron",
        ("negative", "-1", "minus"),
        ("positive", "neutral", "zero"),
    ),
    FactCase(
        "the SI unit of force",
        ("newton",),
        ("joule", "watt", "pascal", "kelvin"),
    ),
    FactCase(
        "what the derivative of a function measures",
        ("rate of change", "slope", "gradient"),
        ("area under", "integral", "average value"),
    ),
]

PERSONA = (
    "You are an experienced examiner. You write questions that distinguish a student who "
    "understands from one who has memorised. Your distractors are the specific mistakes "
    "students actually make, never filler."
)

PROMPT = """\
Write exactly {count} mcq question(s).

TOPIC: {topic}
COGNITIVE LEVEL: remember
DIFFICULTY: easy
MARKS EACH: 1

Give exactly 4 options. Exactly one has is_correct=true.
The option you mark correct must genuinely be the right answer; every distractor must be
genuinely wrong. Each distractor should be a mistake a real student would make.
Set `type` to "mcq", `topic` to "{topic}", `bloom` to "remember", `difficulty` to "easy"
and `marks` to 1 on every question.
"""


# --------------------------------------------------------------------------- scoring


@dataclass
class ModelScore:
    model: str
    size_gb: float = 0.0
    context: int = 0
    fully_on_gpu: bool | None = None

    schema_ok: int = 0
    schema_attempts: list[int] = field(default_factory=list)
    count_ok: int = 0
    facts_right: int = 0
    facts_wrong: int = 0
    facts_unclear: int = 0
    failures: list[str] = field(default_factory=list)
    seconds: list[float] = field(default_factory=list)

    repeats: int = 1

    @property
    def trials(self) -> int:
        return len(FACTS) * self.repeats

    @property
    def schema_rate(self) -> float:
        return self.schema_ok / self.trials if self.trials else 0.0

    @property
    def first_try_rate(self) -> float:
        """How often valid JSON came back without a repair round.

        The repair loop hides a bad model behind extra latency; this is what it costs.
        """
        ones = [a for a in self.schema_attempts if a == 1]
        return len(ones) / len(self.schema_attempts) if self.schema_attempts else 0.0

    @property
    def accuracy(self) -> float:
        graded = self.facts_right + self.facts_wrong
        return self.facts_right / graded if graded else 0.0

    @property
    def median_seconds(self) -> float:
        return statistics.median(self.seconds) if self.seconds else 0.0

    @property
    def verdict(self) -> str:
        """A single number to rank by.

        Weighted towards correctness: a fast model that writes wrong answer keys is worse
        than a slow one that does not. Speed only breaks ties.
        """
        if not self.trials or self.fully_on_gpu is False:
            return "0.00"
        speed_bonus = max(0.0, 1.0 - self.median_seconds / 30.0) * 0.1
        return f"{self.accuracy * 0.6 + self.schema_rate * 0.3 + speed_bonus:.2f}"


def judge(question: Question, case: FactCase) -> str:
    correct = [o for o in question.options if o.is_correct]
    if len(correct) != 1:
        return "unclear"
    marked = correct[0].text.lower()
    if any(token in marked for token in case.right):
        return "right"
    if any(token in marked for token in case.wrong):
        return "wrong"
    return "unclear"


# --------------------------------------------------------------------------- runner


async def evaluate(model: str, count: int = 2, repeats: int = 1) -> ModelScore:
    settings = get_settings()
    provider: LLMProvider = OllamaProvider(
        base_url=settings.llm_base_url,
        model=model,
        temperature=settings.llm_temperature,
        max_tokens=settings.llm_max_tokens,
        num_ctx=settings.llm_num_ctx,
        auto_context=True,
        thinking=settings.llm_thinking,
        timeout=900,
    )
    score = ModelScore(model=model, repeats=repeats)

    try:
        score.context, _ = await provider.effective_context()
        size_bytes = await provider._model_size_bytes()
        score.size_gb = round(size_bytes / 1e9, 1)
    except Exception as exc:
        score.failures.append(f"setup: {exc}")
        return score

    # Sampling is stochastic at temperature > 0, so repeat to separate skill from luck.
    for case in FACTS * repeats:
        prompt = PROMPT.format(count=count, topic=case.topic)
        attempts = 0

        async def on_attempt(n: int, _err: str) -> None:
            nonlocal attempts
            attempts = n

        started = time.perf_counter()
        try:
            batch = await generate_structured(
                provider,
                QuestionBatch,
                [system(PERSONA), user(prompt)],
                max_attempts=3,
                on_attempt=on_attempt,
            )
        except Exception as exc:
            score.failures.append(f"{case.topic[:30]}: {type(exc).__name__}")
            score.seconds.append(time.perf_counter() - started)
            continue

        score.seconds.append(time.perf_counter() - started)
        score.schema_ok += 1
        score.schema_attempts.append(attempts)
        if len(batch.questions) == count:
            score.count_ok += 1

        if batch.questions:
            verdict = judge(batch.questions[0], case)
            setattr(score, f"facts_{verdict}", getattr(score, f"facts_{verdict}") + 1)

    placement = await provider.placement()
    score.fully_on_gpu = placement.get("fully_on_gpu") if placement.get("loaded") else None
    await provider.close()
    return score


def report(scores: list[ModelScore]) -> None:
    print()
    print(
        f"{'model':<20} {'size':>5} {'ctx':>6} {'GPU':>5} "
        f"{'schema':>7} {'1st try':>8} {'count':>6} {'facts':>7} {'median':>7}  score"
    )
    print("-" * 96)
    for s in sorted(scores, key=lambda x: float(x.verdict), reverse=True):
        gpu = "yes" if s.fully_on_gpu else ("NO" if s.fully_on_gpu is False else "?")
        print(
            f"{s.model:<20} {s.size_gb:>4}G {s.context:>6,} {gpu:>5} "
            f"{s.schema_ok:>3}/{s.trials:<3} {s.first_try_rate:>7.0%} "
            f"{s.count_ok:>3}/{s.trials:<2} "
            f"{s.facts_right:>3}/{s.facts_right + s.facts_wrong:<3} "
            f"{s.median_seconds:>6.1f}s  {s.verdict}"
        )
        if s.facts_wrong:
            print(f"{'':<20}   ^ {s.facts_wrong} factually wrong answer key(s)")
        if s.facts_unclear:
            print(f"{'':<20}   ^ {s.facts_unclear} unscorable (ambiguous or malformed)")
        for failure in s.failures[:2]:
            print(f"{'':<20}   ! {failure}")
    print()
    print("schema  = produced a schema-valid QuestionBatch (the real exam schema)")
    print("1st try = did so with no repair round; repairs cost a whole extra generation")
    print("count   = obeyed 'write exactly N questions'")
    print("facts   = marked the genuinely correct option, on checkable science and maths")
    print("score   = 0.6*facts + 0.3*schema + speed tiebreak; 0 if it spills to the CPU")


async def main() -> None:
    import subprocess

    args = sys.argv[1:]
    repeats = 1
    if "--repeat" in args:
        index = args.index("--repeat")
        repeats = int(args[index + 1])
        args = args[:index] + args[index + 2 :]

    requested = args
    if not requested:
        installed = subprocess.run(
            ["ollama", "list"], capture_output=True, text=True, timeout=30
        ).stdout
        requested = [
            line.split()[0]
            for line in installed.splitlines()[1:]
            if line.strip() and not line.startswith("NAME")
        ]

    print(
        f"Evaluating {len(requested)} model(s) on {len(FACTS) * repeats} cases each "
        f"({len(FACTS)} cases x {repeats})."
    )
    print("This loads each model in turn; expect a few minutes per model.\n")

    scores = []
    for model in requested:
        print(f"  {model} ...", flush=True)
        scores.append(await evaluate(model, repeats=repeats))

    report(scores)


if __name__ == "__main__":
    asyncio.run(main())
