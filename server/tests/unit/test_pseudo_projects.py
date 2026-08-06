"""Pseudo project id mapping.

The dangerous case is ``-1``. It is the Favorites pseudo project, but it also sits one
step past the boundary of the saved-filter range, so a ``<=`` where the code needs ``<``
turns a user's favourites into a lookup for saved filter 0 — which does not exist, so it
404s rather than erroring in any way that points at the cause.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy.orm import Session

from calton.config import DatabaseSettings, Settings
from calton.db.base import Base
from calton.db.session import build_engine, session_factory
from calton.permissions.project import max_permissions_for_projects
from calton.permissions.pseudo import (
    FAVORITES_PSEUDO_PROJECT_ID,
    Favorites,
    RealProject,
    SavedFilterProject,
    is_saved_filter,
    project_id_from_saved_filter_id,
    resolve,
    saved_filter_id_from_project_id,
)


class TestFavorites:
    def test_minus_one_is_favorites(self) -> None:
        assert resolve(-1) == Favorites()

    def test_minus_one_is_not_a_saved_filter(self) -> None:
        """The off-by-one this module exists to prevent."""
        assert not is_saved_filter(-1)
        assert not isinstance(resolve(-1), SavedFilterProject)

    def test_favorites_maps_to_filter_zero_which_means_none(self) -> None:
        """-1 does produce 0 from the arithmetic; 0 is upstream's "not a filter" signal,
        and that is exactly why the branch tests for > 0."""
        assert saved_filter_id_from_project_id(-1) == 0

    def test_the_constant_matches_upstream(self) -> None:
        assert FAVORITES_PSEUDO_PROJECT_ID == -1


class TestSavedFilterMapping:
    @pytest.mark.parametrize(
        ("filter_id", "project_id"),
        [(1, -2), (2, -3), (3, -4), (10, -11), (99, -100), (1000, -1001)],
    )
    def test_the_mapping_goes_both_ways(self, filter_id: int, project_id: int) -> None:
        assert project_id_from_saved_filter_id(filter_id) == project_id
        assert saved_filter_id_from_project_id(project_id) == filter_id

    @pytest.mark.parametrize("filter_id", [1, 2, 3, 17, 500])
    def test_the_mapping_is_its_own_inverse(self, filter_id: int) -> None:
        project_id = project_id_from_saved_filter_id(filter_id)

        assert saved_filter_id_from_project_id(project_id) == filter_id

    @pytest.mark.parametrize(("project_id", "filter_id"), [(-2, 1), (-3, 2), (-11, 10)])
    def test_resolve_reports_both_ids(self, project_id: int, filter_id: int) -> None:
        resolved = resolve(project_id)

        assert resolved == SavedFilterProject(filter_id=filter_id, project_id=project_id)

    @pytest.mark.parametrize("project_id", [-2, -3, -50])
    def test_ids_below_minus_one_are_saved_filters(self, project_id: int) -> None:
        assert is_saved_filter(project_id)


class TestRealProjects:
    @pytest.mark.parametrize("project_id", [1, 2, 42, 999999])
    def test_positive_ids_pass_through(self, project_id: int) -> None:
        assert resolve(project_id) == RealProject(id=project_id)

    @pytest.mark.parametrize("project_id", [1, 2, 42])
    def test_positive_ids_are_never_saved_filters(self, project_id: int) -> None:
        assert saved_filter_id_from_project_id(project_id) == 0
        assert not is_saved_filter(project_id)

    def test_zero_is_not_a_pseudo_project(self) -> None:
        """0 means "no project scope" in a task collection, not a virtual project."""
        assert resolve(0) == RealProject(id=0)
        assert not is_saved_filter(0)
        assert saved_filter_id_from_project_id(0) == 0


class TestClamping:
    """Both directions clamp rather than returning a nonsensical id."""

    @pytest.mark.parametrize("project_id", [0, 1, 5, 1000])
    def test_non_filter_project_ids_clamp_to_zero(self, project_id: int) -> None:
        assert saved_filter_id_from_project_id(project_id) == 0

    @pytest.mark.parametrize("filter_id", [-1, -5, -1000])
    def test_negative_filter_ids_clamp_to_zero(self, filter_id: int) -> None:
        assert project_id_from_saved_filter_id(filter_id) == 0

    def test_filter_zero_maps_onto_the_favorites_id(self) -> None:
        """A quirk of the shared arithmetic, harmless because filter ids start at 1.

        Recorded rather than asserted away: if saved filter ids ever start at 0, this
        collides with Favorites and the collision would be silent.
        """
        assert project_id_from_saved_filter_id(0) == FAVORITES_PSEUDO_PROJECT_ID


class TestExhaustiveBoundary:
    """Walk the whole neighbourhood of the boundary rather than sampling around it."""

    @pytest.mark.parametrize("project_id", range(-6, 7))
    def test_every_id_near_zero_classifies_exactly_once(self, project_id: int) -> None:
        resolved = resolve(project_id)

        if project_id == -1:
            assert isinstance(resolved, Favorites)
        elif project_id < -1:
            assert isinstance(resolved, SavedFilterProject)
        else:
            assert isinstance(resolved, RealProject)

    @pytest.mark.parametrize("project_id", range(-6, 7))
    def test_is_saved_filter_agrees_with_the_less_than_minus_one_rule(
        self, project_id: int
    ) -> None:
        """The design doc states the rule as ``project_id < -1``; upstream implements it
        as ``filter_id > 0``. They must agree for every id, not just the sampled ones."""
        assert is_saved_filter(project_id) is (project_id < -1)


class TestLargeValues:
    def test_large_negative_ids_still_map(self) -> None:
        assert saved_filter_id_from_project_id(-1_000_001) == 1_000_000

    def test_go_overflows_where_python_does_not(self) -> None:
        """Documented divergence, unreachable in practice.

        Go computes this in int64, so the most negative id overflows on negation. Python
        has unbounded integers and returns a large positive instead. Project ids come
        from a database sequence, so neither implementation can be reached with a value
        anywhere near the boundary; asserting Python's answer only records that we know.
        """
        int64_min = -(2**63)

        assert saved_filter_id_from_project_id(int64_min) == 2**63 - 1


class TestPseudoIdsNeverReachThePermissionQuery:
    """The permission CTE joins on ``projects``; a pseudo id has no row there.

    Upstream never passes one in — ``checkReadPermissionsForProjects`` peels off
    Favorites and saved filters first. Without a guard, passing one through returns
    "absent", which every caller reads as a denial, so a routing mistake in T16 or T23
    would look like a permissions problem instead of the wiring bug it is.
    """

    @pytest.fixture
    def session(self) -> Iterator[Session]:
        engine = build_engine(Settings(database=DatabaseSettings(path=":memory:")))
        Base.metadata.create_all(engine)
        with session_factory(engine)() as opened:
            yield opened

    @pytest.mark.parametrize("project_id", [-1, -2, -3, -100])
    def test_a_pseudo_id_raises_rather_than_denying(
        self, session: Session, project_id: int
    ) -> None:
        with pytest.raises(ValueError, match="pseudo project ids"):
            max_permissions_for_projects(session, 1, [project_id])

    def test_the_error_names_the_way_out(self, session: Session) -> None:
        with pytest.raises(ValueError, match=r"pseudo\.resolve"):
            max_permissions_for_projects(session, 1, [-2])

    def test_a_pseudo_id_hidden_among_real_ones_is_still_caught(self, session: Session) -> None:
        with pytest.raises(ValueError):
            max_permissions_for_projects(session, 1, [1, 2, -2])

    def test_real_ids_are_unaffected(self, session: Session) -> None:
        assert max_permissions_for_projects(session, 1, [1, 2]) == {}
