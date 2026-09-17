import hashlib
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


# ============================================================
# CONFIGURAÇÕES
# ============================================================

ROOT = Path(__file__).resolve().parents[1]

DATA = ROOT / "data.json"
MANIFEST = ROOT / "update_manifest.json"

MONITORING = ROOT / "monitoring"
SNAPSHOTS = MONITORING / "snapshots"
REPORTS = MONITORING / "reports"
LATEST = MONITORING / "latest.json"

MONITORING.mkdir(exist_ok=True)
SNAPSHOTS.mkdir(exist_ok=True)
REPORTS.mkdir(exist_ok=True)


# ============================================================
# CONFIGURAÇÃO DE ACESSO
# ============================================================

HEADERS_LIST = [
    {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/140.0 Safari/537.36"
        ),
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;"
            "q=0.9,image/avif,image/webp,*/*;q=0.8"
        ),
        "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
        "Connection": "close",
    },
    {
        "User-Agent": "Mozilla/5.0",
        "Accept": "*/*",
        "Connection": "close",
    },
]


MAX_RETRIES = 3
RETRY_DELAY = 4
TIMEOUT = 30


# ============================================================
# FUNÇÕES AUXILIARES
# ============================================================

def now_iso():
    return datetime.now(timezone.utc).isoformat()


def safe_filename(text):
    text = re.sub(r"[^a-zA-Z0-9_-]+", "_", text)
    return text.strip("_")[:100]


def normalize_content(content):
    """
    Remove elementos que normalmente provocam falso positivo:
    scripts, estilos, comentários e espaços excessivos.
    """

    content = re.sub(
        r"<script\b[^>]*>.*?</script>",
        "",
        content,
        flags=re.IGNORECASE | re.DOTALL,
    )

    content = re.sub(
        r"<style\b[^>]*>.*?</style>",
        "",
        content,
        flags=re.IGNORECASE | re.DOTALL,
    )

    content = re.sub(
        r"<!--.*?-->",
        "",
        content,
        flags=re.DOTALL,
    )

    # Remove espaços repetidos
    content = re.sub(r"\s+", " ", content)

    return content.strip()


def fetch_url(url):
    """
    Tenta acessar a fonte várias vezes usando headers diferentes.
    Retorna estrutura padronizada.
    """

    last_error = None

    for attempt in range(1, MAX_RETRIES + 1):

        headers = HEADERS_LIST[(attempt - 1) % len(HEADERS_LIST)]

        try:

            request = Request(url, headers=headers)

            with urlopen(request, timeout=TIMEOUT) as response:

                raw = response.read()

                charset = response.headers.get_content_charset() or "utf-8"

                content = raw.decode(
                    charset,
                    errors="replace"
                )

                return {
                    "success": True,
                    "status": response.status,
                    "content": content,
                    "attempt": attempt,
                    "error": None,
                }

        except HTTPError as error:

            last_error = (
                f"HTTP {error.code} - {error.reason}"
            )

        except URLError as error:

            last_error = (
                f"URL Error: {error.reason}"
            )

        except TimeoutError:

            last_error = "Timeout"

        except Exception as error:

            last_error = (
                f"{type(error).__name__}: {error}"
            )

        if attempt < MAX_RETRIES:
            time.sleep(RETRY_DELAY)

    return {
        "success": False,
        "status": None,
        "content": None,
        "attempt": MAX_RETRIES,
        "error": last_error,
    }


# ============================================================
# CARREGAR FONTES
# ============================================================

if not DATA.exists():

    raise FileNotFoundError(
        f"Arquivo não encontrado: {DATA}"
    )


with DATA.open(
    "r",
    encoding="utf-8"
) as file:

    data = json.load(file)


# ============================================================
# IDENTIFICAR FONTES ÚNICAS
# ============================================================

sources = {}

for item in data:

    if not isinstance(item, dict):
        continue

    url = item.get("source")

    if not url:
        continue

    name = (
        item.get("sourceName")
        or item.get("title")
        or url
    )

    sources[url] = name


print("")
print("=" * 70)
print("MONITORAMENTO REGULATÓRIO")
print("=" * 70)
print(f"Fontes registradas: {len(sources)}")
print("")


# ============================================================
# CARREGAR ÚLTIMO RESULTADO
# ============================================================

previous = {}

if LATEST.exists():

    try:

        with LATEST.open(
            "r",
            encoding="utf-8"
        ) as file:

            previous_data = json.load(file)

            previous = previous_data.get(
                "sources",
                {}
            )

    except Exception:

        previous = {}


# ============================================================
# ESTRUTURAS DE RESULTADO
# ============================================================

verified = []
changes = []
errors = []
recovered = []

current_sources = {}


# ============================================================
# MONITORAR FONTES
# ============================================================

for index, (url, name) in enumerate(
    sources.items(),
    start=1
):

    print(
        f"[{index}/{len(sources)}] {name}"
    )

    result = fetch_url(url)

    # --------------------------------------------------------
    # ERRO DE ACESSO
    # --------------------------------------------------------

    if not result["success"]:

        print(
            f"   ⚠️ ERRO: {result['error']}"
        )

        previous_source = previous.get(url)

        errors.append({
            "name": name,
            "url": url,
            "error": result["error"],
            "attempts": result["attempt"],
        })

        # Mantém o último snapshot válido.
        if previous_source:

            current_sources[url] = previous_source

        continue


    # --------------------------------------------------------
    # PROCESSAMENTO
    # --------------------------------------------------------

    normalized = normalize_content(
        result["content"]
    )

    content_hash = hashlib.sha256(
        normalized.encode("utf-8")
    ).hexdigest()


    previous_source = previous.get(url)

    previous_hash = None

    if previous_source:

        previous_hash = previous_source.get(
            "hash"
        )


    # --------------------------------------------------------
    # IDENTIFICAR MUDANÇA
    # --------------------------------------------------------

    changed = (
        previous_hash is not None
        and previous_hash != content_hash
    )


    # --------------------------------------------------------
    # IDENTIFICAR RECUPERAÇÃO
    # --------------------------------------------------------

    was_error = False

    for old_error in previous_data.get(
        "errors",
        []
    ) if LATEST.exists() else []:

        if old_error.get("url") == url:

            was_error = True
            break


    if was_error:

        recovered.append({
            "name": name,
            "url": url,
        })


    # --------------------------------------------------------
    # REGISTRAR MUDANÇA
    # --------------------------------------------------------

    if changed:

        changes.append({
            "name": name,
            "url": url,
            "previous_hash": previous_hash,
            "current_hash": content_hash,
        })

        print("   🔎 ALTERAÇÃO DETECTADA")

    else:

        print("   ✅ OK")


    # --------------------------------------------------------
    # SNAPSHOT
    # --------------------------------------------------------

    source_id = safe_filename(
        name
    )

    snapshot = {
        "name": name,
        "url": url,
        "checked_at": now_iso(),
        "hash": content_hash,
        "status": result["status"],
    }

    snapshot_file = (
        SNAPSHOTS /
        f"{source_id}.json"
    )

    with snapshot_file.open(
        "w",
        encoding="utf-8"
    ) as file:

        json.dump(
            snapshot,
            file,
            ensure_ascii=False,
            indent=2
        )


    current_sources[url] = snapshot

    verified.append({
        "name": name,
        "url": url,
        "status": result["status"],
        "hash": content_hash,
    })


# ============================================================
# RESULTADO FINAL
# ============================================================

checked_at = now_iso()


result_data = {
    "checked_at": checked_at,

    "summary": {
        "sources_registered": len(sources),
        "sources_verified": len(verified),
        "changes_detected": len(changes),
        "access_errors": len(errors),
        "recovered": len(recovered),
    },

    "sources": current_sources,

    "changes": changes,

    "errors": errors,

    "recovered": recovered,
}


# ============================================================
# SALVAR LATEST.JSON
# ============================================================

with LATEST.open(
    "w",
    encoding="utf-8"
) as file:

    json.dump(
        result_data,
        file,
        ensure_ascii=False,
        indent=2
    )


# ============================================================
# RELATÓRIO MARKDOWN
# ============================================================

today = datetime.now(
    timezone.utc
).strftime("%Y-%m-%d")


report_file = (
    REPORTS /
    f"{today}.md"
)


report = []

report.append(
    f"# Monitoramento Regulatório — {today}"
)

report.append("")

report.append(
    "## Resumo"
)

report.append("")

report.append(
    f"- Fontes registradas: **{len(sources)}**"
)

report.append(
    f"- Fontes verificadas: **{len(verified)}**"
)

report.append(
    f"- Alterações detectadas: **{len(changes)}**"
)

report.append(
    f"- Erros de acesso: **{len(errors)}**"
)

report.append(
    f"- Fontes recuperadas: **{len(recovered)}**"
)

report.append("")


# ------------------------------------------------------------
# ALTERAÇÕES
# ------------------------------------------------------------

if changes:

    report.append(
        "## 🔎 Alterações detectadas"
    )

    report.append("")

    for item in changes:

        report.append(
            f"- **{item['name']}**"
        )

        report.append(
            f"  - {item['url']}"
        )

    report.append("")


# ------------------------------------------------------------
# ERROS
# ------------------------------------------------------------

if errors:

    report.append(
        "## ⚠️ Erros de acesso"
    )

    report.append("")

    for item in errors:

        report.append(
            f"- **{item['name']}**"
        )

        report.append(
            f"  - URL: {item['url']}"
        )

        report.append(
            f"  - Erro: {item['error']}"
        )

    report.append("")


# ------------------------------------------------------------
# RECUPERADAS
# ------------------------------------------------------------

if recovered:

    report.append(
        "## 🔄 Fontes recuperadas"
    )

    report.append("")

    for item in recovered:

        report.append(
            f"- **{item['name']}** — {item['url']}"
        )

    report.append("")


# ------------------------------------------------------------
# STATUS
# ------------------------------------------------------------

if not changes and not errors:

    report.append(
        "## ✅ Status"
    )

    report.append("")

    report.append(
        "Todas as fontes foram verificadas sem "
        "alterações ou erros de acesso."
    )


with report_file.open(
    "w",
    encoding="utf-8"
) as file:

    file.write(
        "\n".join(report)
    )


# ============================================================
# ATUALIZAR MANIFEST
# ============================================================

manifest = {}

if MANIFEST.exists():

    try:

        with MANIFEST.open(
            "r",
            encoding="utf-8"
        ) as file:

            manifest = json.load(file)

    except Exception:

        manifest = {}


manifest.update({

    "verified_at": checked_at,

    "last_weekly_check": checked_at,

    "sources_registered": len(sources),

    "sources_verified": len(verified),

    "last_weekly_changes_detected": len(changes),

    "last_weekly_access_errors": len(errors),

})


with MANIFEST.open(
    "w",
    encoding="utf-8"
) as file:

    json.dump(
        manifest,
        file,
        ensure_ascii=False,
        indent=2
    )


# ============================================================
# RESUMO NO GITHUB ACTIONS
# ============================================================

print("")
print("=" * 70)
print("RESULTADO")
print("=" * 70)

print(
    f"📚 Fontes registradas: {len(sources)}"
)

print(
    f"✅ Fontes verificadas: {len(verified)}"
)

print(
    f"🔎 Alterações detectadas: {len(changes)}"
)

print(
    f"⚠️ Erros de acesso: {len(errors)}"
)

print(
    f"🔄 Fontes recuperadas: {len(recovered)}"
)

print("")


if changes:

    print("ALTERAÇÕES:")

    for item in changes:

        print(
            f" - {item['name']}"
        )

        print(
            f"   {item['url']}"
        )

    print("")


if errors:

    print("ERROS:")

    for item in errors:

        print(
            f" - {item['name']}"
        )

        print(
            f"   {item['error']}"
        )

    print("")


print("=" * 70)
