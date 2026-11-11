"""The message boxes: framed, and answering like QMessageBox's statics.

The autouse `dialogs` fixture replaces the functions on the module, so the
real ones are bound here at import, before it runs. `QMessageBox.exec` is
replaced instead, since a real one would wait for a click offscreen.
"""

from __future__ import annotations

import pytest
from PyQt6.QtWidgets import QMessageBox
from windowchrome import WindowFrame

from crenel.messages import critical, information, question, warning

Button = QMessageBox.StandardButton


@pytest.fixture
def shown(monkeypatch):
    """Every box `exec()`ed, each answered with `shown.click` (if set).

    Like the real `exec()`, the stand-in returns the clicked standard button
    as an int, 0 (NoButton) for none.
    """

    class Shown(list):
        click: Button | None = None

    boxes = Shown()

    def exec_(box):
        boxes.append(box)
        if boxes.click is None:
            return 0
        box.button(boxes.click).click()
        return box.standardButton(box.clickedButton()).value

    monkeypatch.setattr(QMessageBox, "exec", exec_)
    return boxes


@pytest.mark.parametrize(
    "show, icon",
    [
        (critical, QMessageBox.Icon.Critical),
        (warning, QMessageBox.Icon.Warning),
        (information, QMessageBox.Icon.Information),
        (question, QMessageBox.Icon.Question),
    ],
)
def test_each_box_is_framed_with_a_close_button_only(shown, show, icon):
    show(None, "Crenel — Test", "Some text")
    box = shown[-1]
    assert box.windowTitle() == "Crenel — Test"
    assert box.text() == "Some text"
    assert box.icon() == icon
    frame = box.findChild(WindowFrame)
    assert frame is not None and frame.title_bar.buttons == [frame.title_bar.close_button]


def test_the_buttons_default_as_the_statics_do(shown):
    warning(None, "t", "x")
    question(None, "t", "x")
    assert shown[0].standardButtons() == Button.Ok
    assert shown[1].standardButtons() == Button.Yes | Button.No


def test_the_clicked_button_is_returned(shown):
    shown.click = Button.Yes
    assert question(None, "t", "x", Button.Yes | Button.No, Button.No) == Button.Yes
    assert shown[-1].defaultButton() is shown[-1].button(Button.No)
    shown.click = Button.Ok
    assert warning(None, "t", "x") == Button.Ok


def test_nothing_clicked_is_no_button(shown):
    assert information(None, "t", "x", Button.Ok | Button.Cancel) == Button.NoButton
