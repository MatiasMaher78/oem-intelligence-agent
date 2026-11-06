"""One packaged instruction source and allowlisted, secret-free execution provenance."""
from dataclasses import dataclass
from hashlib import sha256
from importlib.resources import files
import re

from .ollama_local import OllamaError
from .stage4_review import fingerprint
from .stage5_contracts import CONTRACT_VERSION

CONFIGURATION_VERSION = "5.4.0"


@dataclass(frozen=True)
class Skill:
    name: str
    version: str
    sha256: str
    body: str


def load_skill(path=None):
    """Read exactly the bundled skill; never discover/include arbitrary instructions."""
    resource = files("oem_agent").joinpath("skills/oem-evidence-review/SKILL.md") if path is None else path
    try:
        with resource.open("rb") as stream:
            data = stream.read(4097)
        if len(data) > 4096:
            raise ValueError()
        text = data.decode("utf-8").replace("\r\n", "\n")
        head, body = text.removeprefix("---\n").split("\n---\n", 1)
        if not text.startswith("---\n") or not body.strip() or len(body.encode("utf-8")) > 1600:
            raise ValueError()
        names = re.findall(r"(?m)^name: ([a-z0-9-]+)$", head)
        versions = re.findall(r'(?m)^  version: "([0-9]+\.[0-9]+\.[0-9]+)"$', head)
        if names != ["oem-evidence-review"] or len(versions) != 1:
            raise ValueError()
        return Skill(names[0], versions[0], sha256(data).hexdigest(), body.strip())
    except (OSError, UnicodeError, ValueError):
        raise OllamaError("invalid_agent_skill") from None


def configuration_record(skill, *, model, provider, limits, dispatcher_limits, contracts, model_info, local_budget=None):
    """Explicit safe fields only: no environment, key, headers, timestamps or thinking."""
    config = {"version": CONFIGURATION_VERSION,
              "skill": {"name": skill.name, "version": skill.version, "sha256": skill.sha256},
              "system_prompt_sha256": sha256(skill.body.encode("utf-8")).hexdigest(),
              "contract_version": CONTRACT_VERSION, "contracts_sha256": fingerprint(contracts),
              "model": model, "provider": provider,
              "execution_limits": limits, "dispatcher_limits": dispatcher_limits}
    if local_budget is not None:
        config["local_batch_budget"] = dict(local_budget)
    if model_info and "operator_confirmed_free_quota" in model_info:
        quota = model_info["operator_confirmed_free_quota"]
        config["operator_confirmed_free_limits"] = {name: quota[name] for name in
            ("requests_per_minute", "requests_per_day", "tokens_per_minute", "tokens_per_day")}
    return config, fingerprint(config)


def answer_references(snapshot, dispatch_report):
    """Only actual accepted reads can supply sources; resolve again before presenting."""
    refs, seen = [], set()
    for event in dispatch_report["trace"]:
        result = event["outcome"]["result"]
        if not result or result["status"] not in ("ok", "partial") or result["data"] is None:
            continue
        for ref in result["references"]:
            snapshot.resolve_reference(ref)
            key = (ref["document"], ref["sha256"], ref["pointer"])
            if key not in seen:
                seen.add(key)
                refs.append({"id": "R"+str(len(refs)+1), "reference": dict(ref)})
    return refs
