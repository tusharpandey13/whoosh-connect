"""#8: first-run steps that advance themselves; no Next button."""
import json

from whoosh_connect import messages
from whoosh_connect.onboarding import STEPS, Onboarding, OnboardingStore, step_for
from whoosh_connect.status import Phase, Status, StatusModel
from whoosh_connect.tray import Tray

ZWIFT = Status(Phase.ERROR, messages.ZWIFT.text, messages.ZWIFT)
BT_OFF = Status(Phase.ERROR, messages.BT_OFF.text, messages.BT_OFF)
SEARCH = Status(Phase.SEARCHING, "Searching")
WAIT = Status(Phase.WAITING, "Waiting")
RIDE = Status(Phase.RIDING, "Riding")


def guide(tmp_path):
    notes, lines = [], []
    g = Onboarding(notify=lambda text, title: notes.append((text, title)), show=lines.append,
                   store=OnboardingStore(tmp_path / "app.json"))
    return g, notes, lines


def test_step_mapping():
    assert [step_for(s) for s in (ZWIFT, SEARCH, WAIT, RIDE)] == [1, 2, 3, 4]
    assert step_for(BT_OFF) is None  # some other problem: its own sentence shows instead


def test_full_first_run_auto_advances(tmp_path):
    g, notes, lines = guide(tmp_path)
    for s in (ZWIFT, SEARCH, WAIT, RIDE):
        g.update(s)
    assert [t for t, _ in notes] == [STEPS[1], STEPS[2], STEPS[3], STEPS[4]]
    assert notes[2][1] == "whoosh-connect setup (3 of 4)"
    assert lines[-1] == "Setup: step 4 of 4 - " + STEPS[4]
    assert "OpenBikeControl" in STEPS[3] and "whoosh-connect" in STEPS[3]
    assert OnboardingStore(tmp_path / "app.json").done()


def test_zwift_not_running_starts_at_step_two(tmp_path):
    g, notes, _ = guide(tmp_path)
    g.update(SEARCH)
    assert notes == [(STEPS[2], "whoosh-connect setup (2 of 4)")]


def test_one_notice_per_step_and_never_backwards(tmp_path):
    g, notes, lines = guide(tmp_path)
    for s in (SEARCH, SEARCH, WAIT, SEARCH, WAIT, BT_OFF, WAIT):
        g.update(s)
    assert [t for t, _ in notes] == [STEPS[2], STEPS[3]] and len(lines) == 2


def test_straight_to_riding_still_teaches_the_press_window(tmp_path):
    g, notes, _ = guide(tmp_path)
    g.update(RIDE)
    assert [t for t, _ in notes] == [STEPS[4]]


def test_done_means_silent_next_launch(tmp_path):
    g, _, _ = guide(tmp_path)
    g.update(RIDE)
    g2, notes, lines = guide(tmp_path)
    assert not g2.active
    for s in (ZWIFT, SEARCH, WAIT, RIDE):
        g2.update(s)
    assert notes == [] and lines == []


def test_store_keeps_other_keys_and_survives_garbage(tmp_path):
    p = tmp_path / "app.json"
    p.write_text("not json")
    assert not OnboardingStore(p).done()
    p.write_text(json.dumps({"other": 1}))
    OnboardingStore(p).mark_done()
    assert json.loads(p.read_text()) == {"other": 1, "onboarded": True}


def test_store_is_not_state_json(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert OnboardingStore().path.name == "app.json"


def test_unwritable_store_does_not_raise(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("")
    OnboardingStore(blocker / "app.json").mark_done()  # parent is a file


def test_driven_by_the_real_status_model(tmp_path):
    g, notes, _ = guide(tmp_path)
    m = StatusModel(g.update)
    m.set_error(messages.ZWIFT)
    m.clear_error()
    m.set_link(True, 90)
    m.set_apps(1)
    assert [t for t, _ in notes] == [STEPS[1], STEPS[2], STEPS[3], STEPS[4]]


# -- menu line ----------------------------------------------------------------------------
from test_app import FakeMenu, FakePystray  # noqa: E402


def test_tray_shows_setup_line_only_while_set():
    t = Tray(quit=lambda: None, pystray=FakePystray, on_main=lambda f: f(), image=str)
    setup = [i for i in t.icon.menu.items if i is not FakeMenu.SEPARATOR][1]
    assert not setup.shown()
    t.set_setup("Setup: step 2 of 4 - " + STEPS[2])
    assert setup.shown() and setup.label().startswith("Setup: step 2 of 4")
    assert t.icon.menu_updates == 1
