"""
Lays a guest's matches out in score order so you can see where they go wrong.

    python scripts/review_matches.py

Writes an HTML page and opens it. Lowest-scoring matches come first, because
that is where false positives live: scroll until the faces stop being the same
person, read the score there, and that is roughly where the threshold belongs.

Cheaper than tune_threshold.py, which wants photographs already sorted one
folder per person. This needs no labelling at all, and answers the only
question that matters first: is the current threshold letting strangers in.
"""

from __future__ import annotations

import pathlib
import sys
import webbrowser

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from sqlalchemy import text  # noqa: E402

from app.config import settings  # noqa: E402
from app.db import system_session  # noqa: E402

OUT = pathlib.Path("match_review.html")


def main(argv: list[str]) -> int:
    session_id = argv[1] if len(argv) > 1 else None

    with system_session() as db:
        if session_id is None:
            session_id = db.execute(
                text(
                    "SELECT gs.id FROM guest_sessions gs"
                    " JOIN guest_matches m ON m.guest_session_id = gs.id"
                    " GROUP BY gs.id ORDER BY count(*) DESC LIMIT 1"
                )
            ).scalar()
            if session_id is None:
                print("  no guest has matched anything yet.")
                return 1

        rows = db.execute(
            text(
                """
                SELECT m.similarity, p.storage_key, p.thumb_key, p.filename, p.face_count
                  FROM guest_matches m
                  JOIN photos p ON p.id = m.photo_id
                 WHERE m.guest_session_id = :gs
                 ORDER BY m.similarity ASC
                """
            ),
            {"gs": str(session_id)},
        ).all()

    if not rows:
        print("  that guest has no matches.")
        return 1

    root = pathlib.Path(settings().local_storage_dir).resolve()
    threshold = settings().match_threshold

    cards = []
    for sim, storage_key, thumb_key, filename, faces in rows:
        src = (root / (thumb_key or storage_key)).as_uri()
        cards.append(
            f'<figure><img src="{src}" loading="lazy" alt="">'
            f'<figcaption><b>{sim:.3f}</b>'
            f'<span>{(filename or "")[:28]} · {faces} face(s)</span>'
            f"</figcaption></figure>"
        )

    OUT.write_text(
        f"""<!doctype html><meta charset="utf-8"><title>Match review</title>
<style>
 body{{font:14px system-ui;margin:0;background:#111;color:#eee}}
 header{{position:sticky;top:0;background:#111;padding:16px 20px;border-bottom:1px solid #333}}
 h1{{margin:0 0 4px;font-size:17px}}
 p{{margin:0;color:#999}}
 .grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(190px,1fr));gap:14px;padding:20px}}
 figure{{margin:0}}
 img{{width:100%;aspect-ratio:1;object-fit:cover;border-radius:5px;background:#222;display:block}}
 figcaption{{display:flex;flex-direction:column;gap:1px;padding-top:5px;font-size:12px;color:#888}}
 figcaption b{{font-size:15px;color:#eee}}
</style>
<header>
 <h1>{len(rows)} matches, weakest first</h1>
 <p>Current threshold {threshold:.3f}. Scroll until these stop being the same
    person; the score where that happens is where the threshold belongs.</p>
</header>
<div class="grid">{"".join(cards)}</div>
""",
        encoding="utf-8",
    )

    print(f"  {len(rows)} matches, {rows[0][0]:.3f} to {rows[-1][0]:.3f}")
    print(f"  threshold is {threshold:.3f}")
    print(f"  opening {OUT.resolve()}")
    webbrowser.open(OUT.resolve().as_uri())
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
