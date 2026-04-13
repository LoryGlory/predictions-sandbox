#!/usr/bin/env python3
"""Export collected stories as formatted markdown or JSON for blog posts.

Usage:
    python scripts/export_stories.py --format markdown
    python scripts/export_stories.py --format json --blog-post part3
    python scripts/export_stories.py --format markdown --unused-only
"""
import argparse
import asyncio
import json
import logging
import sys

from src.db.connection import get_db
from src.tracking.logger import setup_logging

logger = logging.getLogger(__name__)

STORY_TYPE_LABELS = {
    "big_edge": "Big Edge Prediction",
    "spectacular_win": "Spectacular Win",
    "spectacular_failure": "Spectacular Failure",
    "category_milestone": "Category Milestone",
    "cost_milestone": "Cost Milestone",
    "system": "System Event",
}


def _format_markdown(stories: list[dict]) -> str:
    """Format stories as markdown for blog posts."""
    if not stories:
        return "No stories to export.\n"

    lines: list[str] = []
    current_blog_post = None

    for story in stories:
        blog = story.get("blog_post") or "uncategorized"
        if blog != current_blog_post:
            current_blog_post = blog
            lines.append(f"\n# Blog Post: {blog}\n")

        label = STORY_TYPE_LABELS.get(story["story_type"], story["story_type"])
        lines.append(f"## {label} — {story['created_at'][:10]}")

        try:
            details = json.loads(story["details"])
        except (json.JSONDecodeError, TypeError):
            details = {"raw": story["details"]}

        if "question" in details:
            lines.append(f"**Market:** \"{details['question']}\"")
        if "estimated_prob" in details:
            lines.append(f"**Claude estimated:** {details['estimated_prob']:.0%}")
        if "market_price" in details:
            lines.append(f"**Market price:** {details['market_price']:.0%}")
        if "actual_outcome" in details:
            outcome = "YES" if details["actual_outcome"] == 1 else "NO"
            lines.append(f"**Outcome:** {outcome}")
        if "claude_brier" in details:
            lines.append(
                f"**Brier:** Claude {details['claude_brier']:.2f}, "
                f"Market {details.get('market_brier', 'N/A'):.2f}"
            )
        if "reasoning" in details and details["reasoning"]:
            lines.append(f"**Reasoning:** {details['reasoning'][:200]}")

        lines.append("")

    return "\n".join(lines)


async def export_stories(
    fmt: str = "markdown",
    blog_post: str | None = None,
    unused_only: bool = True,
    mark_used: bool = True,
) -> str:
    """Export stories from the database."""
    setup_logging()

    async with get_db() as db:
        where_parts = []
        params: list = []

        if unused_only:
            where_parts.append("used = 0")
        if blog_post:
            where_parts.append("blog_post = ?")
            params.append(blog_post)

        where = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""

        async with db.execute(
            f"""SELECT * FROM stories {where}
               ORDER BY blog_post, story_type, created_at""",
            params,
        ) as cur:
            stories = [dict(r) for r in await cur.fetchall()]

        if not stories:
            return "No stories to export.\n"

        if fmt == "json":
            output = json.dumps(stories, indent=2, default=str)
        else:
            output = _format_markdown(stories)

        # Mark exported stories as used
        if mark_used and stories:
            ids = [s["id"] for s in stories]
            placeholders = ",".join("?" * len(ids))
            await db.execute(
                f"UPDATE stories SET used = 1 WHERE id IN ({placeholders})", ids,
            )
            await db.commit()
            logger.info("Marked %d stories as used", len(stories))

    return output


def main() -> None:
    parser = argparse.ArgumentParser(description="Export blog stories")
    parser.add_argument("--format", choices=["markdown", "json"], default="markdown")
    parser.add_argument("--blog-post", choices=["part1", "part2", "part3", "part4"])
    parser.add_argument(
        "--unused-only", action="store_true", default=True,
        help="Only export stories not yet used (default)",
    )
    parser.add_argument(
        "--all", action="store_true", dest="export_all",
        help="Export all stories, including already used ones",
    )
    parser.add_argument(
        "--no-mark-used", action="store_true",
        help="Don't mark exported stories as used",
    )
    args = parser.parse_args()

    output = asyncio.run(export_stories(
        fmt=args.format,
        blog_post=args.blog_post,
        unused_only=not args.export_all,
        mark_used=not args.no_mark_used,
    ))
    sys.stdout.write(output)


if __name__ == "__main__":
    main()
