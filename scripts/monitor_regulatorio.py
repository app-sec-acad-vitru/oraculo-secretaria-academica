import hashlib
import json
import re
import urllib.request
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
        "Oraculo-Secretaria-Academica-Regulatory-Monitor/1.0 "
        "(GitHub Actions)"
    )
}


# ============================================================
# CONSULTA À FONTE
# ============================================================

def fetch(url):
    """
    Consulta uma fonte oficial e retorna:
    - código HTTP
    - conteúdo da página
    """

    request = urllib.request.Request(
        url,
        headers=HEADERS
    )

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

        return response.status, text


# ============================================================
# NORMALIZAÇÃO
# ============================================================

def normalize(text):
    """
    Remove variações de espaços para permitir
    comparação mais consistente entre verificações.
    """

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

    # --------------------------------------------------------
    # CARREGA BASE DO ORÁCULO
    # --------------------------------------------------------

    if not DATA.exists():
        raise FileNotFoundError(
            "Arquivo data.json não encontrado."
        )

    data = json.loads(
        DATA.read_text(
            encoding="utf-8"
        )
    )

    # --------------------------------------------------------
    # IDENTIFICA FONTES
    # --------------------------------------------------------

    urls = {}

    for item in data:

        url = item.get("source")

        if url:

            source_name = item.get(
                "sourceName",
                "Fonte oficial"
            )

            urls[url] = source_name

    print(
        f"Fontes encontradas no data.json: {len(urls)}"
    )

    # --------------------------------------------------------
    # CARREGA RESULTADO DA ÚLTIMA VERIFICAÇÃO
    # --------------------------------------------------------

    previous_file = MONITOR / "latest.json"

    previous = {}

    if previous_file.exists():

        previous = json.loads(
            previous_file.read_text(
                encoding="utf-8"
            )
        )

    # --------------------------------------------------------
    # LISTAS DE RESULTADOS
    # --------------------------------------------------------

    results = []

    changes = []

    errors = []

    # --------------------------------------------------------
    # VERIFICA CADA FONTE
    # --------------------------------------------------------

    for url, source_name in sorted(
        urls.items()
    ):

        print(
            f"Verificando: {source_name}"
        )

        result = {

            "source": source_name,

            "url": url,

            "checked_at": checked_at

        }

        try:

            status, body = fetch(url)

            normalized = normalize(
                body
            )

            digest = hashlib.sha256(
                normalized.encode(
                    "utf-8"
                )
            ).hexdigest()

            result["http_status"] = status

            result["sha256"] = digest

            result["ok"] = (
                200 <= status < 400
            )

            # ------------------------------------------------
            # COMPARA COM A VERIFICAÇÃO ANTERIOR
            # ------------------------------------------------

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

            else:

                result[
                    "changed_since_last_check"
                ] = False

            # ------------------------------------------------
            # SALVA SNAPSHOT
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

        except Exception as error:

            result["ok"] = False

            result["error"] = str(
                error
            )

            result[
                "changed_since_last_check"
            ] = False

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
    # GERA RELATÓRIO
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

        "## Resultado",

        "",

        f"- Fontes verificadas: "
        f"**{len(results)}**",

        f"- Fontes com alteração detectada: "
        f"**{len(changes)}**",

        f"- Fontes com erro de acesso: "
        f"**{len(errors)}**",

        "",

        "> A detecção de alteração em uma página "
        "não significa, por si só, alteração normativa. "
        "Toda mudança deve ser analisada no conteúdo "
        "oficial antes de atualizar a base do Oráculo.",

        "",

        "## Fontes verificadas",

        ""
    ]

    for result in results:

        if result.get(
            "changed_since_last_check"
        ):

            flag = "⚠️ ALTERAÇÃO"

        elif result.get("ok"):

            flag = "🟢 OK"

        else:

            flag = "🔴 ERRO"

        report.append(

            f"- {flag} — "
            f"{result['source']}: "
            f"{result['url']}"

        )

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
    # RESULTADO NO GITHUB ACTIONS
    # ========================================================

    print("")
    print(
        "========================================"
    )

    print(
        "VERIFICAÇÃO CONCLUÍDA"
    )

    print(
        "========================================"
    )

    print(
        f"Fontes verificadas: {len(results)}"
    )

    print(
        f"Alterações detectadas: {len(changes)}"
    )

    print(
        f"Erros: {len(errors)}"
    )

    print(
        "========================================"
    )

    if changes:

        print("")
        print(
            "ALTERAÇÕES DETECTADAS:"
        )

        for item in changes:

            print(
                f"- {item['source']}"
            )

            print(
                f"  {item['url']}"
            )

    else:

        print("")
        print(
            "Nenhuma alteração detectada."
        )


# ============================================================
# EXECUÇÃO
# ============================================================

if __name__ == "__main__":

    main()
