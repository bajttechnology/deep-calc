import os
import sys
import glob
import json
import ctypes
import threading

import webview


def resource(name):
    """Súbory zabalené vnútri .exe (HTML, ikona)."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


def app_dir():
    """Priečinok, v ktorom leží samotné .exe (tu hľadáme model)."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def search_dirs():
    dirs = []
    for d in (app_dir(), os.path.dirname(os.path.abspath(sys.argv[0])), os.getcwd()):
        if d and d not in dirs:
            dirs.append(d)
    return dirs


def find_model():
    for d in search_dirs():
        files = sorted(glob.glob(os.path.join(d, "*.gguf")))
        if files:
            return files[0]
    return None


SYSTEM_PROMPTS = {
    "sk": (
        "You turn a math word problem (usually in Slovak) into a formula and ONE arithmetic expression. "
        "Work in this order: first translate the problem to English (problem_en), then think briefly "
        "about which relationship connects the quantities (reasoning), then name the formula in Slovak "
        "(formula), say which number goes where in Slovak (substitution), and finally write the "
        "expression. Do NOT compute the final answer. The expression contains the real numbers from the "
        "problem already substituted, and may use only numbers with a decimal point, + - * / ^ ( ), "
        "the functions sin cos tan asin acos atan sqrt ln log abs and the constants pi and e. "
        "No units, no variable names, no % sign (write percent as a division by 100), no equals sign. "
        "Reply ONLY with JSON."
    ),
    "en": (
        "You turn a math word problem (usually in English, but it may also be in Slovak) into a formula "
        "and ONE arithmetic expression. Work in this order: first translate or restate the problem in "
        "English (problem_en), then briefly explain which relationship connects the quantities "
        "(reasoning), name the formula in English (formula), explain which number goes where in English "
        "(substitution), and finally write the expression. Do NOT compute the final answer. The expression "
        "contains the real numbers from the problem already substituted, and may use only numbers with a "
        "decimal point, + - * / ^ ( ), the functions sin cos tan asin acos atan sqrt ln log abs and the "
        "constants pi and e. No units, no variable names, no % sign (write percent as a division by 100), "
        "no equals sign. Reply ONLY with JSON."
    ),
}


SCHEMA = {
    "type": "object",
    "properties": {
        "problem_en": {"type": "string"},
        "reasoning": {"type": "string"},
        "formula": {"type": "string"},
        "substitution": {"type": "string"},
        "expression": {"type": "string"},
    },
    "required": ["problem_en", "reasoning", "formula", "substitution", "expression"],
}

EXAMPLES = {
    "sk": [
        (
            "Auto ide rýchlosťou 80 km/h počas 2,5 hodiny. Akú dráhu prejde?",
            {
                "problem_en": "A car drives at 80 km/h for 2.5 hours. How far does it travel?",
                "reasoning": "distance equals speed times time",
                "formula": "s = v · t",
                "substitution": "v = 80 km/h, t = 2,5 h",
                "expression": "80*2.5",
            },
        ),
        (
            "Dvaja robotníci vykopú za 10 minút 4 m² zeme. Za koľko minút vykopú tí istí dvaja robotníci 10 m²?",
            {
                "problem_en": "Two workers dig 4 square metres in 10 minutes. How many minutes do the same two workers need for 10 square metres?",
                "reasoning": "same workers, so the dug area is directly proportional to time: 4 m2 takes 10 min, so 10 m2 takes 10 * 10 / 4 min",
                "formula": "Priama úmera: t₂ = t₁ · (S₂ / S₁)",
                "substitution": "t₁ = 10 min, S₁ = 4 m², S₂ = 10 m²",
                "expression": "10*10/4",
            },
        ),
        (
            "Cena tovaru je 120 € a zvýši sa o 15 %. Aká je nová cena?",
            {
                "problem_en": "A product costs 120 euros and the price rises by 15 percent. What is the new price?",
                "reasoning": "new price equals old price times (1 plus percent divided by 100)",
                "formula": "C₂ = C₁ · (1 + p/100)",
                "substitution": "C₁ = 120 €, p = 15",
                "expression": "120*(1+15/100)",
            },
        ),
    ],
    "en": [
        (
            "A car travels at 80 km/h for 2.5 hours. How far does it travel?",
            {
                "problem_en": "A car travels at 80 km/h for 2.5 hours. How far does it travel?",
                "reasoning": "Distance equals speed multiplied by time.",
                "formula": "s = v · t",
                "substitution": "v = 80 km/h, t = 2.5 h",
                "expression": "80*2.5",
            },
        ),
        (
            "Two workers dig 4 square metres in 10 minutes. How long will the same workers need to dig 10 square metres?",
            {
                "problem_en": "Two workers dig 4 square metres in 10 minutes. How long will the same workers need to dig 10 square metres?",
                "reasoning": "With the same workers, the area dug is directly proportional to time.",
                "formula": "Direct proportion: t₂ = t₁ · (S₂ / S₁)",
                "substitution": "t₁ = 10 min, S₁ = 4 m², S₂ = 10 m²",
                "expression": "10*10/4",
            },
        ),
        (
            "A product costs 120 euros and its price increases by 15 percent. What is the new price?",
            {
                "problem_en": "A product costs 120 euros and its price increases by 15 percent. What is the new price?",
                "reasoning": "The new price equals the original price multiplied by one plus the percentage divided by 100.",
                "formula": "C₂ = C₁ · (1 + p/100)",
                "substitution": "C₁ = 120 €, p = 15",
                "expression": "120*(1+15/100)",
            },
        ),
    ],
}


def build_messages(text, language="sk"):
    language = "en" if language == "en" else "sk"
    msgs = [{"role": "system", "content": SYSTEM_PROMPTS[language]}]
    for question, answer in EXAMPLES[language]:
        msgs.append({"role": "user", "content": question})
        msgs.append({"role": "assistant", "content": json.dumps(answer, ensure_ascii=False)})
    msgs.append({"role": "user", "content": str(text)[:1500]})
    return msgs



class Api:
    """Funkcie, ktoré vie z kalkulačky zavolať JavaScript."""

    def __init__(self):
        self.model_path = find_model()
        self._llm = None
        self._lock = threading.Lock()

    def ai_status(self):
        return {
            "available": bool(self.model_path),
            "model": os.path.basename(self.model_path) if self.model_path else "",
            "folders": search_dirs(),
        }

    def solve(self, text, language="sk"):
        language = "en" if language == "en" else "sk"
        if not self.model_path:
            error = "No .gguf model file was found next to the .exe." if language == "en" else "Vedľa .exe sa nenašiel žiadny súbor .gguf."
            return {"ok": False, "error": error}
        try:
            with self._lock:
                if self._llm is None:
                    from llama_cpp import Llama  # načíta sa až pri prvom použití

                    self._llm = Llama(
                        model_path=self.model_path,
                        n_ctx=4096,
                        n_threads=max(1, (os.cpu_count() or 4) // 2),
                        verbose=False,
                    )
                out = self._llm.create_chat_completion(
                    messages=build_messages(text, language),
                    response_format={"type": "json_object", "schema": SCHEMA},
                    temperature=0.1,
                    max_tokens=700,
                )
            data = json.loads(out["choices"][0]["message"]["content"])
            return {
                "ok": True,
                "reasoning": str(data.get("reasoning", "")),
                "formula": str(data.get("formula", "")),
                "substitution": str(data.get("substitution", "")),
                "expression": str(data.get("expression", "")),
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}


# Vlastné ID aplikácie: na paneli úloh bude ikona kalkulačky, nie Pythonu.
if sys.platform == "win32":
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("daniel.kalkulacka")
    except Exception:
        pass

webview.create_window(
    "DeepCalc",
    resource("kalkulacka.html"),
    js_api=Api(),
    width=920,
    height=700,
    min_size=(360, 560),
)
webview.start(icon=resource("calc.ico"))
