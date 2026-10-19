"""Model providers. OfflineProvider is a scripted test model, not a language model."""
import ast
import json
import os
import re
from types import SimpleNamespace
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen


def prompt_text(prompt):
    return prompt if isinstance(prompt, str) else "\n".join(m.content for m in prompt)


class OfflineProvider:
    name = "Offline fixture model — integration testing only"
    def __init__(self, search):
        self.search = search
        self.calls = []
        self.confidence = 90
        self.resolved = True
        self.wrapping_up = False
        self.selection = None
    def invoke(self, prompt):
        text = prompt_text(prompt)
        self.calls.append(text)
        # Unknown prompts raise an error instead of returning a made-up answer.
        query_match = re.search(r'(?:Student query|Current Query|Current query|Query):\s*"(.*?)"', text, re.S | re.I)
        query = query_match.group(1) if query_match else ""
        low = query.lower()
        if "query intent classifier" in text:
            intent = "definition" if low.startswith(("what is", "define")) else "explanation"
            if any(x in low for x in ("compare", "difference")): intent = "comparison"
            if any(x in low for x in ("confused", "don't understand", "struggling")): intent = "difficulty"
            if any(x in low for x in ("assignment", "rubric", "deadline")): intent = "other"
            result = {"intent": intent, "reasoning": "Offline scripted intent"}
        elif "GENUINELY STRUGGLING" in text:
            result = {"struggling": False, "reason": "Offline fixture: no inferred historical struggle"}
        elif "just confirmed they understood" in text:
            result = "You're welcome! Glad that helped."
        elif "Generate a short, relevant, and engaging title" in text:
            result = "Statistics practice"
        elif "domain filter" in text or "Final check, using the same test" in text:
            result = "yes" if self.search.search(query) or any(w in low for w in ("variance", "explain", "understand")) else "no"
        elif "helping an educational chatbot understand" in text:
            ambiguous = low.strip("?. ") == "help with variance"
            result = {"is_ambiguous": ambiguous, "scenarios": ["Entire population variance", "Estimate from a sample"] if ambiguous else []}
        elif "Analyze if the current query continues" in text:
            result = {"is_related": False, "relevant_query": "", "relevant_response": ""}
        elif "conversational PACING" in text:
            result = {"end_likelihood": 85 if self.wrapping_up else 15, "likely_wrapping_up": self.wrapping_up, "reasoning": "Controlled offline pacing fixture"}
        elif "successfully resolved a student's concern" in text:
            result = {"confidence": self.confidence, "resolved": self.resolved, "reasoning": "Controlled offline resolution fixture"}
        elif "AVAILABLE SURVEY QUESTIONS" in text:
            tail = text.split("AVAILABLE SURVEY QUESTIONS", 1)[1]
            available = ast.literal_eval(re.search(r"\[.*?\]", tail, re.S).group(0))
            chosen = self.selection if self.selection is not None else available[:2]
            result = {"confidence": "high", "reasoning": "Fixture selects first two available items; not a semantic judgment", "selected_questions": [{"question_text": q, "reasoning": "Fixture"} for q in chosen]}
        elif '"suggestions"' in text:
            result = {"suggestions": ["Compare mean and median", "Explain sample variance"]}
        elif "categorising a student feedback" in text:
            result = "teaching"
        elif "selected a specific scenario" in text:
            sample = 'specific situation: "Estimate from a sample"' in text
            result = {"response": "Use sample variance: divide the squared deviations by n-1. For 2, 4, 6 this is 8/2 = 4." if sample else "Use population variance: divide the squared deviations by N. For 2, 4, 6 this is 8/3."}
        elif '"response"' in text:
            docs = self.search.search(query, top=1)
            result = {"relevant": bool(docs), "response": docs[0]["content"] if docs else "The sample course pack does not cover that question. Try a named topic from the course pack."}
        else:
            raise ValueError("Offline provider has no fixture for this prompt type")
        return SimpleNamespace(content=result if isinstance(result, str) else json.dumps(result))


class AzureProvider:
    """Live Azure OpenAI model.

    Reads the four FYP_AZURE_* environment variables and sends the algorithm's
    prompts unchanged. It never loads a .env file by itself.
    """
    name = "Azure language model — live"
    def __init__(self):
        values = {k: os.environ.get(k, "") for k in ("FYP_AZURE_ENDPOINT", "FYP_AZURE_DEPLOYMENT", "FYP_AZURE_API_VERSION", "FYP_AZURE_API_KEY")}
        if not all(values.values()): raise ValueError("Set all four FYP_AZURE_* variables described in README.md")
        endpoint = values["FYP_AZURE_ENDPOINT"].rstrip("/")
        parsed = urlparse(endpoint)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.query:
            raise ValueError("Azure endpoint must be an HTTPS resource endpoint")
        self.url = endpoint + "/openai/deployments/" + quote(values["FYP_AZURE_DEPLOYMENT"], safe="") + "/chat/completions?api-version=" + quote(values["FYP_AZURE_API_VERSION"], safe="")
        self.key = values["FYP_AZURE_API_KEY"]
    def invoke(self, prompt):
        messages = [{"role": "user", "content": prompt}] if isinstance(prompt, str) else [{"role": "assistant" if m.type == "ai" else "user", "content": m.content} for m in prompt]
        request = Request(self.url, data=json.dumps({"messages": messages}).encode(), headers={"Content-Type": "application/json", "api-key": self.key})
        with urlopen(request, timeout=45) as response:
            body = json.load(response)
        return SimpleNamespace(content=body["choices"][0]["message"]["content"])
