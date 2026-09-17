import hashlib
import json
import re
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

# ============================================================
# ORÁCULO DA SECRETARIA ACADÊMICA
# Monitoramento semanal de fontes regulatórias
# ============================================================

ROOT = Path(__file__).resolve().parents[1]

DATA = ROOT / "data.json"
MANIFEST = ROOT / "update_manifest.json"

MONITOR = ROOT / "monitoring"
SNAPSHOTS = MONITOR / "snapshots"
REPORTS = MONITOR / "reports"

MONITOR.mkdir(exist_ok=True)
SNAPSHOTS.mkdir(exist_ok=True)
REPORTS.mkdir(exist_ok=True)

TIMEOUT = 30

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 "
        "(compatible; Oraculo-Secretaria-Academica/1.0; "
        "+https://github.com/app-sec-acad-vitru/oraculo-secretaria-academica)"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
}


# ============================================================
# CONSULTA À FONTE
# ============================================================

def fetch(url):

    request = urllib.request.Request(
        url,
        headers=HEADERS
    )

    try:

        with urllib.request.urlopen(
            request,
            timeout=TIMEOUT
        ) as response:

            raw = response.read()

            content_type = response.headers.get(
                "Content-Type",
                ""
            )

            charset = "utf-8"

            match = re.search(
                r"charset=([\w-]+)",
                content_type,
                re.I
            )

            if match:
                charset = match.group(1)

            try:

                text = raw.decode(
                    charset,
                    errors="replace"
                )

            except LookupError:

                text = raw.decode(
                    "utf-8",
                    errors="replace"
                )

            return {
                "success": True,
                "status": response.status,
                "text": text,
                "error": None
            }

    except urllib.error.HTTPError as error:

        return {
            "success": False,
            "status": error.code,
            "text": "",
            "error": f"HTTP {error.code}: {error.reason}"
        }

    except urllib.error.URLError as error:

        return {
            "success": False,
            "status": None,
            "text": "",
            "error": f"URL Error: {error.reason}"
        }

    except Exception as error:

        return {
            "success": False,
            "status": None,
            "text": "",
            "error": str(error)
        }


# ============================================================
# NORMALIZAÇÃO
# ============================================================

def normalize(text):

    text = re.sub(
        r"\s+",
        " ",
        text
    )

    return text.strip()


# ============================================================
# PROCESSAMENTO
# ============================================================

def main():

    now = datetime.now(timezone.utc)

    checked_at = now.isoformat().replace(
        "+00:00",
        "Z"
    )

    # ========================================================
    # CARREGA DATA.JSON
    # ========================================================

    if not DATA.exists():

        raise FileNotFoundError(
            "Arquivo data.json não encontrado."
        )

    data = json.loads(
        DATA.read_text(
            encoding="utf-8"
        )
    )

    # ========================================================
    # IDENTIFICA FONTES
    # ========================================================

    urls = {}

    for item in data:

        url = item.get("source")

        if url:

            source_name = item.get(
                "sourceName",
                "Fonte oficial"
            )

            urls[url] = source_name

    total_sources = len(urls)

    print("")
    print("========================================")
    print("ORÁCULO DA SECRETARIA ACADÊMICA")
    print("MONITORAMENTO REGULATÓRIO")
    print("========================================")
    print("")
    print(
        f"Fontes cadastradas no data.json: {total_sources}"
    )
    print("")

    # ========================================================
    # CARREGA ÚLTIMA VERIFICAÇÃO
    # ========================================================

    previous_file = MONITOR / "latest.json"

    previous = {}

    if previous_file.exists():

        try:

            previous = json.loads(
                previous_file.read_text(
                    encoding="utf-8"
                )
            )

        except Exception:

            previous = {}

    # ========================================================
    # RESULTADOS
    # ========================================================

    results = []

    successful = []

    changes = []

    errors = []

    # ========================================================
    # VERIFICAÇÃO DAS FONTES
    # ========================================================

    for url, source_name in sorted(
        urls.items()
    ):

        print(
            f"Verificando: {source_name}"
        )

        response = fetch(url)

        result = {

            "source": source_name,

            "url": url,

            "checked_at": checked_at,

            "http_status": response["status"],

            "ok": response["success"],

            "changed_since_last_check": False

        }

        # ----------------------------------------------------
        # FONTE ACESSÍVEL
        # ----------------------------------------------------

        if response["success"]:

            normalized = normalize(
                response["text"]
            )

            digest = hashlib.sha256(
                normalized.encode(
                    "utf-8"
                )
            ).hexdigest()

            result["sha256"] = digest

            old = previous.get(
                url,
                {}
            )

            if (
                old.get("sha256")
                and old["sha256"] != digest
            ):

                result[
                    "changed_since_last_check"
                ] = True

                changes.append(
                    result
                )

            successful.append(
                result
            )

            # ------------------------------------------------
            # SNAPSHOT
            # ------------------------------------------------

            snapshot_name = (
                hashlib.sha256(
                    url.encode("utf-8")
                ).hexdigest()[:16]
                + ".txt"
            )

            snapshot_file = (
                SNAPSHOTS
                / snapshot_name
            )

            snapshot_file.write_text(
                normalized[:500000],
                encoding="utf-8"
            )

        # ----------------------------------------------------
        # ERRO
        # ----------------------------------------------------

        else:

            result["error"] = response["error"]

            errors.append(
                result
            )

        results.append(
            result
        )

    # ========================================================
    # SALVA ÚLTIMO RESULTADO
    # ========================================================

    latest = {}

    for result in results:

        latest[
            result["url"]
        ] = result

    previous_file.write_text(

        json.dumps(
            latest,
            ensure_ascii=False,
            indent=2
        ),

        encoding="utf-8"
    )

    # ========================================================
    # RELATÓRIO
    # ========================================================

    report_name = (
        now.strftime(
            "%Y-%m-%d"
        )
        + ".md"
    )

    report = [

        f"# Verificação regulatória — "
        f"{now.strftime('%d/%m/%Y')}",

        "",

        f"Executada em: `{checked_at}`",

        "",

        "## Resumo",

        "",

        f"- Fontes cadastradas: **{total_sources}**",

        f"- Fontes verificadas com sucesso: **{len(successful)}**",

        f"- Alterações detectadas: **{len(changes)}**",

        f"- Erros de acesso: **{len(errors)}**",

        "",

        "> Uma alteração detectada em uma página não significa, "
        "por si só, alteração normativa. Toda mudança deve ser "
        "analisada no conteúdo oficial antes da atualização "
        "da base do Oráculo.",

        "",

        "## Fontes verificadas",

        ""
    ]

    # ========================================================
    # FONTES COM SUCESSO
    # ========================================================

    for result in successful:

        if result.get(
            "changed_since_last_check"
        ):

            flag = "⚠️ ALTERAÇÃO"

        else:

            flag = "🟢 OK"

        report.append(

            f"- {flag} — "
            f"{result['source']} — "
            f"{result['url']}"

        )

    # ========================================================
    # FONTES COM ERRO
    # ========================================================

    if errors:

        report.extend(
            [
                "",
                "## ⚠️ Fontes com erro de acesso",
                ""
            ]
        )

        for result in errors:

            report.append(

                f"- 🔴 **{result['source']}**"
            )

            report.append(

                f"  - URL: {result['url']}"
            )

            report.append(

                f"  - Erro: {result['error']}"
            )

            report.append("")

    # ========================================================
    # SALVA RELATÓRIO
    # ========================================================

    report_file = (
        REPORTS
        / report_name
    )

    report_file.write_text(

        "\n".join(report)
        + "\n",

        encoding="utf-8"
    )

    # ========================================================
    # ATUALIZA MANIFEST
    # ========================================================

    if MANIFEST.exists():

        manifest = json.loads(

            MANIFEST.read_text(
                encoding="utf-8"
            )

        )

    else:

        manifest = {}

    manifest[
        "verified_at"
    ] = now.strftime(
        "%Y-%m-%d"
    )

    manifest[
        "last_weekly_check"
    ] = checked_at

    manifest[
        "sources_registered"
    ] = total_sources

    manifest[
        "sources_verified"
    ] = len(successful)

    manifest[
        "last_weekly_changes_detected"
    ] = len(changes)

    manifest[
        "last_weekly_access_errors"
    ] = len(errors)

    MANIFEST.write_text(

        json.dumps(
            manifest,
            ensure_ascii=False,
            indent=2
        )
        + "\n",

        encoding="utf-8"
    )

    # ========================================================
    # RESULTADO FINAL
    # ========================================================

    print("")
    print("========================================")
    print("VERIFICAÇÃO CONCLUÍDA")
    print("========================================")

    print(
        f"Fontes cadastradas: {total_sources}"
    )

    print(
        f"Fontes verificadas com sucesso: {len(successful)}"
    )

    print(
        f"Alterações detectadas: {len(changes)}"
    )

    print(
        f"Erros de acesso: {len(errors)}"
    )

    print("========================================")

    # ========================================================
    # LISTA DE ERROS
    # ========================================================

    if errors:

        print("")
        print("FONTES COM ERRO:")
        print("")

        for result in errors:

            print(
                f"🔴 {result['source']}"
            )

            print(
                f"   URL: {result['url']}"
            )

            print(
                f"   Erro: {result['error']}"
            )

            print("")

    # ========================================================
    # ALTERAÇÕES
    # ========================================================

    if changes:

        print("")
        print("ALTERAÇÕES DETECTADAS:")
        print("")

        for result in changes:

            print(
                f"⚠️ {result['source']}"
            )

            print(
                f"   {result['url']}"
            )

    else:

        print("")
        print(
            "Nenhuma alteração detectada nas fontes acessíveis."
        )

    print("")


# ============================================================
# EXECUÇÃO
# ============================================================

if __name__ == "__main__":

    main()
