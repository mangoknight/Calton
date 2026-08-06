"""Pseudo project ids.

Not every project id refers to a row in ``projects``. Negative ids address two virtual
projects, and every place that accepts a ``project_id`` has to route through
:func:`resolve` before touching the database — a raw lookup for a negative id silently
finds nothing and reports "not found" for something that does exist.

``-1`` is the Favorites pseudo project (``project.go:153``). Everything below ``-1`` is a
saved filter, mapped by ``id * -1 - 1`` in both directions
(``saved_filters.go:72-90``) — filter 1 is project -2, filter 2 is project -3.

**``-1`` being taken by Favorites is the whole reason the saved-filter test is
``< -1`` and not ``<= -1``.** Off by one there and the Favorites project resolves to
saved filter 0, which does not exist, so the user's favourites silently 404.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from calton.models.saved_filter import SavedFilter

#: ``project.go:153``. Favorites has no row; it is assembled from the favorites table.
FAVORITES_PSEUDO_PROJECT_ID = -1


@dataclass(frozen=True)
class RealProject:
    """An ordinary project with a row of its own."""

    id: int


@dataclass(frozen=True)
class Favorites:
    """The Favorites pseudo project, id ``-1``."""

    id: int = FAVORITES_PSEUDO_PROJECT_ID


@dataclass(frozen=True)
class SavedFilterProject:
    """A saved filter addressed as a project."""

    filter_id: int
    project_id: int


PseudoProject = RealProject | Favorites | SavedFilterProject


def saved_filter_id_from_project_id(project_id: int) -> int:
    """The saved filter a project id addresses, or ``0`` when it addresses none.

    Transcribed from ``GetSavedFilterIDFromProjectID``. Zero is upstream's "not a saved
    filter" signal — callers there test ``> 0`` — which is what keeps ``-1`` (Favorites,
    which maps to 0) and every real project id out of the saved-filter branch.
    """
    filter_id = project_id * -1 - 1
    # Filter ids derived from project ids are always positive.
    if filter_id < 0:
        return 0
    return filter_id


def project_id_from_saved_filter_id(filter_id: int) -> int:
    """The project id a saved filter is addressed by.

    ``getProjectIDFromSavedFilterID`` — the same arithmetic as the other direction with
    the opposite clamp, which is what makes the mapping its own inverse.
    """
    project_id = filter_id * -1 - 1
    # Project ids derived from saved filters are always negative.
    if project_id > 0:
        return 0
    return project_id


def resolve(project_id: int) -> PseudoProject:
    """Classify a project id before anything is looked up.

    Ordering matters: Favorites is checked first, because ``-1`` would otherwise fall
    through to the saved-filter branch and resolve to filter 0.
    """
    if project_id == FAVORITES_PSEUDO_PROJECT_ID:
        return Favorites()

    filter_id = saved_filter_id_from_project_id(project_id)
    if filter_id > 0:
        return SavedFilterProject(filter_id=filter_id, project_id=project_id)

    return RealProject(id=project_id)


def is_saved_filter(project_id: int) -> bool:
    """Whether this id addresses a saved filter rather than a project or Favorites."""
    return saved_filter_id_from_project_id(project_id) > 0


def load_saved_filter(session: Session, filter_id: int) -> SavedFilter:
    """The saved filter behind a pseudo project id, or its 404.

    Measured: a pseudo id naming no filter answers **404/11001**
    ``"This saved filter does not exist."`` — *not* the 3001 a missing project gets, and
    not a 403. The distinction is visible to any client that walks the negative range.
    """
    from calton.core.errors import CaltonError

    stored = session.get(SavedFilter, filter_id)
    if stored is None:
        raise CaltonError.from_name("models.ErrSavedFilterDoesNotExist")
    return stored


def can_read_saved_filter(session: Session, user_id: int, filter_id: int) -> bool:
    """Whether the caller may read a saved filter — ``SavedFilter.CanRead``.

    ⚠️ **Shared on purpose.** Every resource addressed through a saved filter's pseudo
    project id asks this same question, and the answer must not be spelled twice: T17's
    view endpoints call it, and T29 owns the filter endpoints that ask it about the same
    rows. Two transcriptions of one rule drift, and the drift shows up as "the filter is
    readable through one URL and not another".

    Ownership only, which is what upstream checks here. Link shares are Phase 2 and would
    widen this — when they land, widen it *here* rather than at a call site.
    """
    return load_saved_filter(session, filter_id).owner_id == user_id
