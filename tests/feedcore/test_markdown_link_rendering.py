from feedcore.models import TypeFourDimRecord
from feedcore.workflow.concurrent import render_brief_html
from feedcore.workflow.reading_topics import render_reading_topic_brief_html


def test_render_brief_html_renders_markdown_links_with_brackets_in_label():
    markdown = "\n".join(
        [
            "# Brief",
            "",
            "## Category",
            "",
            "### Topic",
            "",
            "#### 来源",
            r"- [\[download\] Windows 10 update](https://example.com/update)",
            "",
        ]
    )

    html = render_brief_html(markdown)

    assert '<a href="https://example.com/update">[download] Windows 10 update</a>' in html
    assert r'[\[download\] Windows 10 update](https://example.com/update)' not in html


def test_reading_topic_html_renders_source_links_with_brackets_in_label():
    record = TypeFourDimRecord(
        type_id="type_001",
        name="System Update",
        category_group="Technology",
        facts=["Windows 10 update released."],
        background=[],
        impact=[],
        contradictions=[],
        source_refs=[{"title": "[download] Windows 10 update", "url": "https://example.com/update"}],
    )

    html = render_reading_topic_brief_html(
        [
            type(
                "Category",
                (),
                {
                    "category_group": "Technology",
                    "intro": "Updates shipped.",
                    "topics": [
                        type(
                            "Topic",
                            (),
                            {
                                "name": "System Updates",
                                "facts": ["Windows 10 update released."],
                                "background": [],
                                "impact": [],
                                "contradictions": [],
                                "type_records": [record],
                            },
                        )()
                    ],
                },
            )()
        ],
        report_date="2026-05-12",
    )

    assert '<a href="https://example.com/update">[download] Windows 10 update</a>' in html
    assert r'[\[download\] Windows 10 update](https://example.com/update)' not in html
