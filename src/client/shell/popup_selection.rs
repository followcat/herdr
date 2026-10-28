use super::*;
use crossterm::event::{MouseButton, MouseEvent, MouseEventKind};

// Popup surfaces expose visible cells rather than pane history. Keep selection
// in the client and copy those cells; no server or wire-format change is needed.
fn cell_span(frame: &FrameData, row: u16, column: u16, width: u16) -> (u16, u16) {
    let mut left = 0;
    while left < width {
        let Some(cell) = frame
            .cells
            .get(usize::from(row) * usize::from(frame.width) + usize::from(left))
        else {
            break;
        };
        let cell_width = u16::try_from(UnicodeWidthStr::width(cell.symbol.as_str()))
            .unwrap_or(u16::MAX)
            .max(1);
        let right = left.saturating_add(cell_width).min(width);
        if column < right {
            return (left, right - 1);
        }
        left = right;
    }
    (column, column)
}

fn visible_selection(
    selection: &crate::selection::Selection<String>,
    frame: &FrameData,
    inner: Rect,
) -> Option<crate::selection::Selection<()>> {
    let width = frame.width.min(inner.width);
    let height = frame.height.min(inner.height);
    if !selection.is_visible() || width == 0 || height == 0 {
        return None;
    }
    let ((mut first_row, mut first_col), (last_row, last_col)) = selection.ordered_cells();
    let clipped_last_row = last_row.min(u32::from(height - 1));
    if first_col >= width {
        first_row = first_row.saturating_add(1);
        first_col = 0;
    }
    if first_row > clipped_last_row {
        return None;
    }
    let last_col = if clipped_last_row < last_row {
        width - 1
    } else {
        last_col.min(width - 1)
    };
    let first_col = cell_span(frame, first_row as u16, first_col, width).0;
    let last_col = cell_span(frame, clipped_last_row as u16, last_col, width).1;
    Some(crate::selection::Selection::absolute_range(
        (),
        (first_row, first_col),
        (clipped_last_row, last_col),
    ))
}

impl ClientShellState {
    pub(super) fn clear_popup_selection(&mut self, outcome: &mut ClientShellInput) {
        outcome.repaint |= self.popup_selection.take().is_some();
    }

    pub(super) fn handle_popup_mouse_selection(
        &mut self,
        hit: &PaneHit,
        mouse: MouseEvent,
        outcome: &mut ClientShellInput,
    ) -> bool {
        if !self.config.mouse_capture {
            self.clear_popup_selection(outcome);
            return false;
        }
        match mouse.kind {
            MouseEventKind::Down(MouseButton::Left)
                if !hit.mouse_reporting
                    && super::contains(hit.inner_rect, (mouse.column, mouse.row)) =>
            {
                self.popup_selection = Some(crate::selection::Selection::anchor(
                    hit.pane_id.clone(),
                    mouse.row - hit.inner_rect.y,
                    mouse.column - hit.inner_rect.x,
                    None,
                ));
                outcome.repaint = true;
                true
            }
            MouseEventKind::Drag(MouseButton::Left) | MouseEventKind::Up(MouseButton::Left) => {
                let Some(selection) = self.popup_selection.as_mut().filter(|selection| {
                    selection.pane_id == hit.pane_id && selection.is_in_progress()
                }) else {
                    return false;
                };
                selection.drag(mouse.column, mouse.row, hit.inner_rect, None);
                if mouse.kind == MouseEventKind::Up(MouseButton::Left) {
                    if selection.finish() {
                        if self.config.copy_on_select {
                            self.copy_popup_selection(outcome);
                            self.clear_popup_selection(outcome);
                        }
                    } else {
                        self.clear_popup_selection(outcome);
                    }
                    outcome.repaint = true;
                } else {
                    outcome.repaint |=
                        self.request_selection_drag_repaint(std::time::Instant::now());
                }
                true
            }
            MouseEventKind::Down(_)
            | MouseEventKind::ScrollUp
            | MouseEventKind::ScrollDown
            | MouseEventKind::ScrollLeft
            | MouseEventKind::ScrollRight => {
                self.clear_popup_selection(outcome);
                false
            }
            _ => false,
        }
    }

    pub(super) fn copy_popup_selection(&mut self, outcome: &mut ClientShellInput) -> bool {
        let Some(selection) = self.popup_selection.as_ref() else {
            return false;
        };
        let Some(popup) = self
            .pane_surface
            .as_ref()
            .and_then(|surface| surface.popup.as_deref())
            .filter(|popup| popup.terminal_id == selection.pane_id)
        else {
            return false;
        };
        let Some(hit) = self
            .hits
            .popup
            .as_ref()
            .filter(|hit| hit.pane_id == popup.terminal_id)
        else {
            return false;
        };
        let frame = &popup.frame;
        let Some(selection) = visible_selection(selection, frame, hit.inner_rect) else {
            return false;
        };
        let ((first_row, first_col), (last_row, last_col)) = selection.ordered_cells();
        let width = frame.width.min(hit.inner_rect.width);
        let mut text = String::new();
        for row in first_row..=last_row {
            if row != first_row {
                text.push('\n');
            }
            let left = if row == first_row { first_col } else { 0 };
            let right = if row == last_row { last_col } else { width - 1 };
            let start = text.len();
            let mut column = 0;
            while column <= right {
                let Some(cell) = frame
                    .cells
                    .get(row as usize * usize::from(frame.width) + usize::from(column))
                else {
                    break;
                };
                if column >= left {
                    if cell.symbol.is_empty() {
                        text.push(' ');
                    } else {
                        text.push_str(&cell.symbol);
                    }
                }
                let cell_width = u16::try_from(UnicodeWidthStr::width(cell.symbol.as_str()))
                    .unwrap_or(u16::MAX)
                    .max(1);
                column = column.saturating_add(cell_width);
            }
            if right == width - 1 {
                let trimmed = text[start..].trim_end_matches(' ').len();
                text.truncate(start + trimmed);
            }
        }
        outcome
            .actions
            .push(ClientShellAction::ClipboardWrite(text.into_bytes()));
        outcome.repaint |= self.show_copy_feedback(std::time::Instant::now());
        true
    }

    pub(super) fn render_popup_selection(
        &self,
        frame: &mut FrameData,
        popup_frame: &FrameData,
        terminal_id: &str,
        inner: Rect,
    ) -> Option<()> {
        let selection = self
            .popup_selection
            .as_ref()
            .filter(|selection| selection.pane_id == terminal_id)
            .and_then(|selection| visible_selection(selection, popup_frame, inner));
        let Some(selection) = selection else {
            return Some(());
        };
        let cursor = frame.cursor.clone();
        let mut buffer = frame.to_ratatui_buffer()?;
        crate::ui::render_selection_highlight(
            Some(&selection),
            &mut buffer,
            &(),
            inner,
            None,
            &self.config.palette,
            crate::terminal_theme::TerminalTheme {
                background: self.host_background,
                ..Default::default()
            },
        );
        frame.replace_from_ratatui_buffer_preserving_effects(&buffer, cursor);
        Some(())
    }
}
