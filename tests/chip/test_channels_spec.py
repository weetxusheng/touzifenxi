from __future__ import annotations

from datetime import date

from chip.channels.spec import CHANNELS, ChipChannelSpec
from utils.tools.content_models import RawArticleDetail, RawArticleRef


def test_channels_registry_contains_two_channels():
    assert set(CHANNELS.keys()) == {"semi", "laoyaoba"}


def test_channel_spec_fields():
    for key, spec in CHANNELS.items():
        assert isinstance(spec, ChipChannelSpec)
        assert spec.key == key
        assert spec.name
        assert spec.listing_urls
        assert callable(spec.fetch_listing)
        assert callable(spec.fetch_article)


def test_channels_have_distinct_names():
    names = {spec.name for spec in CHANNELS.values()}
    assert len(names) == len(CHANNELS)
