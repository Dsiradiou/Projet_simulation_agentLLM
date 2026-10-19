"""Historique de tous les runs interrogé avec DuckDB : vues communes et requêtes nommées (F5).

Les vues lisent directement les fichiers Parquet de `runs/*/evenements/` et les manifestes
`runs/*/manifest.json` : il n'y a pas de base à alimenter, et une requête donne le même résultat
tant que les fichiers ne changent pas. Les requêtes nommées servent aux résultats du rapport.

Usage :
    python -m replay.historique                          # liste des runs
    python -m replay.historique --requete sens_par_jour  # une requête nommée
    python -m replay.historique --sql "SELECT ..."       # une requête libre sur les vues
"""

import argparse
import sys
from pathlib import Path

import duckdb

from sim.run import RUNS_DIR

# Vues construites sur la table brute `evenements`. Les champs du payload sont extraits du JSON
# brut : ils restent donc exactement ceux qui ont circulé sur le bus.
VUES = {
    "decisions": """
        SELECT run_id, jour, sim_time_occurred, event_id, source_agent_id AS agent_id, zone,
               brut->>'$.payload.sens' AS sens,
               brut->>'$.payload.action' AS action,
               CAST(brut->>'$.payload.porteur' AS BOOLEAN) AS porteur,
               brut->>'$.payload.moteur' AS moteur,
               brut->>'$.payload.palier' AS palier,
               brut->>'$.payload.appel_llm.modele' AS modele,
               CAST(brut->>'$.payload.appel_llm.tokens_entree' AS INTEGER) AS tokens_entree,
               CAST(brut->>'$.payload.appel_llm.tokens_sortie' AS INTEGER) AS tokens_sortie,
               CAST(brut->>'$.payload.appel_llm.duree_s' AS DOUBLE) AS duree_s,
               CAST(brut->>'$.payload.appel_llm.tentatives' AS INTEGER) AS tentatives,
               brut->>'$.payload.justification' AS justification,
               caused_by
        FROM evenements WHERE type = 'agent.decision'""",
    "messages": """
        SELECT run_id, jour, event_id, source_agent_id AS agent_id, zone,
               brut->>'$.payload.canal' AS canal,
               brut->>'$.payload.contenu' AS contenu,
               CAST(brut->>'$.payload.livraison' AS BIGINT) AS livraison,
               caused_by
        FROM evenements WHERE type = 'agent.message'""",
    "incidents": """
        SELECT run_id, jour, event_id,
               brut->>'$.payload.nature' AS nature,
               brut->>'$.payload.agent_id' AS agent_id,
               CAST(brut->>'$.payload.tentative' AS INTEGER) AS tentative,
               brut->>'$.payload.detail' AS detail,
               caused_by
        FROM evenements WHERE type = 'incident'""",
}

REQUETES = {
    "runs": """
        SELECT run_id, config_name, seed, started_at, count(e.event_id) AS evenements
        FROM runs LEFT JOIN evenements e USING (run_id)
        GROUP BY ALL ORDER BY started_at""",
    "sens_par_jour": """
        SELECT run_id, jour, sens, count(*) AS decisions
        FROM decisions GROUP BY ALL ORDER BY run_id, jour, sens""",
    "cout_par_modele": """
        SELECT run_id, modele, sum(tentatives) AS appels, sum(tokens_entree) AS tokens_entree,
               sum(tokens_sortie) AS tokens_sortie, round(sum(duree_s), 1) AS duree_s
        FROM decisions WHERE modele IS NOT NULL GROUP BY ALL ORDER BY run_id, modele""",
    "incidents_par_nature": """
        SELECT run_id, nature, count(*) AS incidents
        FROM incidents GROUP BY ALL ORDER BY run_id, nature""",
    "adoption_par_jour": """
        WITH jours AS (
            SELECT run_id, unnest(range(1, max(jour) + 1)) AS jour FROM decisions GROUP BY run_id
        ),
        etat AS (  -- dernière décision de chaque agent à la fin de chaque jour
            SELECT j.run_id, j.jour, d.agent_id,
                   arg_max(d.porteur, d.sim_time_occurred) AS porteur,
                   arg_max(d.sens, d.sim_time_occurred) AS sens
            FROM jours j JOIN decisions d ON d.run_id = j.run_id AND d.jour <= j.jour
            GROUP BY ALL
        )
        SELECT run_id, jour, count(*) FILTER (porteur) AS porteurs, count(*) AS agents,
               count(*) FILTER (sens = 'efficace') AS efficace,
               count(*) FILTER (sens = 'tendance') AS tendance,
               count(*) FILTER (sens = 'banal') AS banal,
               count(*) FILTER (sens = 'depasse') AS depasse
        FROM etat GROUP BY run_id, jour ORDER BY run_id, jour""",
    "messages_par_role": """
        SELECT run_id, regexp_extract(agent_id, '^(.*)_[0-9]+$', 1) AS role, canal,
               count(*) AS messages
        FROM messages GROUP BY ALL ORDER BY run_id, role, canal""",
    "decisions_par_motif": """
        SELECT run_id, jour, brut_motif AS motif, count(*) AS decisions
        FROM (SELECT run_id, jour, brut->>'$.payload.motif' AS brut_motif FROM evenements
              WHERE type = 'agent.decision')
        GROUP BY ALL ORDER BY run_id, jour, motif""",
    "messages_par_palier": """
        SELECT m.run_id, d.palier, m.canal, count(*) AS messages
        FROM messages m JOIN decisions d ON d.event_id = m.caused_by[1]
        GROUP BY ALL ORDER BY m.run_id, d.palier, m.canal""",
}


def connexion(runs_dir: Path = RUNS_DIR) -> duckdb.DuckDBPyConnection:
    """Connexion en mémoire avec les vues `evenements`, `runs`, `decisions`, `messages`,
    `incidents` sur l'historique de tous les runs."""
    con = duckdb.connect()
    racine = runs_dir.as_posix()
    if any(runs_dir.glob("*/evenements/part-*.parquet")):
        con.execute(
            f"CREATE VIEW evenements AS SELECT * FROM read_parquet("
            f"'{racine}/*/evenements/part-*.parquet', union_by_name = true)"
        )
    else:  # aucun historique : vue vide, avec le bon schéma
        from replay.ecrivain import SCHEMA

        con.register("evenements_vide", SCHEMA.empty_table())
        con.execute("CREATE VIEW evenements AS SELECT * FROM evenements_vide")
    if any(runs_dir.glob("*/manifest.json")):
        con.execute(
            f"CREATE VIEW runs AS SELECT run_id, config_name, seed, started_at "
            f"FROM read_json_auto('{racine}/*/manifest.json', union_by_name = true)"
        )
    else:
        con.execute(
            "CREATE VIEW runs AS SELECT NULL::VARCHAR AS run_id, NULL::VARCHAR AS config_name, "
            "NULL::BIGINT AS seed, NULL::VARCHAR AS started_at WHERE false"
        )
    for nom, sql in VUES.items():
        con.execute(f"CREATE VIEW {nom} AS {sql}")
    return con


def requete(nom: str, runs_dir: Path = RUNS_DIR) -> list[tuple]:
    return connexion(runs_dir).execute(REQUETES[nom]).fetchall()


def main() -> None:
    parser = argparse.ArgumentParser(description="Interroge l'historique des runs (DuckDB).")
    groupe = parser.add_mutually_exclusive_group()
    groupe.add_argument("--requete", choices=sorted(REQUETES), help="requête nommée")
    groupe.add_argument("--sql", help="requête libre sur les vues")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    con = connexion()
    con.sql(args.sql or REQUETES[args.requete or "runs"]).show(max_rows=200, max_width=200)


if __name__ == "__main__":
    main()
