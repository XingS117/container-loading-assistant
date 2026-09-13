from app.fixed_regions import FloorRect, choose_region, free_floor_regions


def test_locked_floor_rect_is_removed_from_free_regions():
    regions = free_floor_regions(1000, 1000, [FloorRect(400, 400, 200, 200)])
    assert all(not (region.x < 600 and region.x + region.length > 400 and region.y < 600 and region.y + region.width > 400) for region in regions)
    assert sum(region.length * region.width for region in regions) == 1000 * 1000 - 200 * 200


def test_non_overlapping_lock_does_not_change_region():
    assert free_floor_regions(1000, 1000, [FloorRect(1200, 0, 100, 100)]) == [FloorRect(0, 0, 1000, 1000)]


def test_choose_region_finds_rotatable_region():
    assert choose_region([FloorRect(0, 0, 300, 500)], 400, 200) == FloorRect(0, 0, 300, 500)


def test_choose_region_returns_none_when_no_region_fits():
    assert choose_region([FloorRect(0, 0, 300, 300)], 400, 200) is None
