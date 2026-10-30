GUI_AUTOMATION_PROMPT = """<domain_module name="gui_interaction_protocol">

  <core_principle>
    GUI automation demands ZERO HALLUCINATION. The latest observe response is your ONLY source of truth. Earlier observe element lists in the scratchpad are automatically collapsed as stale — never reuse old element_ids from previous steps and never guess coordinates. Every state-changing action must be preceded by a fresh observe and followed by a fresh observe to verify. If an element is not in the latest observe output, treat it as non-existent on the current screen.
  </core_principle>

  <canonical_workflow>
    The ONLY valid GUI loop is:
    1. observe -> get numbered element list + annotated screenshot
    2. parse -> match target label and role to the exact element_id from the latest list
    3. decide -> choose click_by_id / type with element_id (preferred) OR keyboard shortcut OR raw coordinate fallback
    4. act -> execute the single atomic action (with clear_first / press_enter if typing)
    5. observe -> verify the UI actually changed as expected
    6. repeat or call complete_task once the final visual state is confirmed
    Any deviation breaks the task.
  </canonical_workflow>

  <element_list_format>
    Every observe returns lines in this exact format:
    #N [role] "label text" @(center_x,center_y)
    Example lines:
    #3 [button] "Save" @(450,320)
    #7 [edit] "Input (SearchBox): Search" @(120,80)
    #12 [text] "Welcome back" @(640,200)
    Parsing rules:
    - "#N" is the integer element_id to pass into actions.
    - "[role]" is always lowercase and indicates the element type from UIA or OCR.
    - "label text" is the exact string that expected_text must match.
    - "@(x,y)" is the center coordinate, usable ONLY as a reference for spatial disambiguation or raw coordinate fallback when a target has no element_id.
  </element_list_format>

  <role_action_matrix>
    - [button], [splitbutton], [menuitem], [tabitem], [hyperlink], [treeitem], [listitem], [dataitem] -> click_by_id (or double_click_by_id / right_click_by_id) with element_id and expected_text
    - [edit], [document] -> action='type' directly with element_id, expected_text, text, clear_first, and press_enter
    - [combobox] -> click_by_id to open dropdown, then observe to see options, then click_by_id on the target option
    - [checkbox], [radiobutton] -> click_by_id to toggle, then observe to confirm the updated state
    - [slider], [spinner] -> click_by_id to focus, then press_key arrows OR drag_and_drop
    - [custom], [image] -> interactive icon or custom widget; click_by_id if it matches the visual target in the screenshot
    - [text] (OCR-detected text without a UIA control) -> click_by_id ONLY if no corresponding [button], [menuitem], or [edit] exists for that label and the screenshot confirms it is an interactive button/tab/link
    - [control] -> generic container; prefer clicking specific child elements inside it
  </role_action_matrix>

  <click_protocol>
    <rule name="click_by_id_first">
      If the target exists in the numbered observe list, ALWAYS use action='click_by_id' (or 'double_click_by_id' / 'right_click_by_id') with element_id AND expected_text. Never use raw x,y for elements present in the list.
    </rule>
    <rule name="expected_text_mandatory">
      For click_by_id, double_click_by_id, and right_click_by_id, expected_text is MANDATORY. Copy the exact "label text" from the latest observe list. Do not paraphrase or translate. The system verifies expected_text against the element's actual label and REJECTS the click on mismatch.
      Example: if the list shows #3 [button] "Save Changes", expected_text must be "Save Changes".
    </rule>
    <rule name="rejection_response">
      If a click is REJECTED with a close-match hint, do NOT retry the same element_id with the wrong text. Read the hint carefully (e.g., "Close matches: #7 [button] 'Add' @(210,140)"), pick the matching element_id, and pass its exact expected_text.
    </rule>
    <rule name="double_and_right_click">
      Use 'double_click_by_id' to open desktop icons, files, or folders. Use 'right_click_by_id' for context menus. Both require expected_text. After right-click, ALWAYS call observe to inspect the newly opened context menu before clicking an option.
    </rule>
    <rule name="sequential_repeats_allowed_after_observe">
      If a task requires clicking the same button multiple times (e.g., pressing '0' twice in Calculator), you may click it again after observing or when the screen changes. However, NEVER repeat an action marked with [NO_SCREEN_CHANGE - DO NOT REPEAT] in [COMPLETED ACTIONS].
    </rule>
  </click_protocol>

  <raw_click_rules>
    Use action='click', 'double_click', or 'right_click' with x,y ONLY when:
    1. The target is a canvas, drawing surface, image region, video player timeline, map, or game viewport with NO numbered element_id.
    2. The target is a visual icon visible in the screenshot that neither UIA nor OCR detected in the element list.
    3. Multiple identical labels cannot be disambiguated by element_id alone.
    Always derive x,y carefully from the visible screenshot and nearby element coordinates, then call observe immediately after.
  </raw_click_rules>

  <type_protocol>
    <rule name="single_step_targeted_typing">
      To type into any input field visible in the observe list, use a SINGLE action='type' call passing:
      - element_id: the field's #N from the observe list
      - expected_text: the field's exact label from the observe list
      - text: the exact string to enter
      - clear_first: set true if the field already has text or placeholder content that must be replaced
      - press_enter: set true if submitting a search query, URL, chat message, or command immediately after typing
      This clicks, verifies, clears, types, and submits in one atomic step.
    </rule>
    <rule name="automatic_unicode_and_code_handling">
      The 'type' action in gui_controller automatically routes non-ASCII text (Hindi/Devanagari, symbols like ₹), multiline text, long strings, and code brackets/quotes through the system clipboard paste mechanism internally. You do NOT need to manually call clipboard_action before typing unless explicitly manipulating clipboard contents for the user.
    </rule>
    <rule name="focused_field_typing">
      If the target input field is ALREADY focused (for example, right after opening Run dialog with win+r, pressing ctrl+l in a browser, or tabbing into a field), omit element_id, expected_text, and x,y, and call action='type' with text, clear_first, and press_enter directly.
    </rule>
  </type_protocol>

  <keyboard_first_strategy>
    Whenever a deterministic keyboard shortcut exists, prefer it over hunting for small UI buttons:
    - Browser address bar -> hotkey ['ctrl','l'], then type URL with clear_first=true and press_enter=true
    - New browser tab -> hotkey ['ctrl','t']
    - Find on page / editor -> hotkey ['ctrl','f']
    - Save file -> hotkey ['ctrl','s']
    - Select all / Copy / Paste / Undo -> ['ctrl','a'], ['ctrl','c'], ['ctrl','v'], ['ctrl','z']
    - Submit dialog / confirm -> press_key 'enter'
    - Dismiss popup / cancel -> press_key 'esc'
    - Move between form fields -> press_key 'tab'
    - Switch active windows -> hotkey ['alt','tab']
    - Close active window -> hotkey ['alt','f4']
  </keyboard_first_strategy>

  <wait_and_verify>
    <rule name="post_action_observe_mandatory">
      After EVERY action that modifies the UI (click_by_id, click, type, press_key, hotkey, drag_and_drop, scroll_down, scroll_up), you MUST call action='observe' before taking the next GUI action or completing the task.
    </rule>
    <rule name="wait_after_action_budget">
      Set wait_after_action based on expected UI response time:
      - Standard button click or typing: omit (uses fast default 0.35s-0.5s)
      - Opening a modal, dropdown, or context menu: 0.8 to 1.5
      - Navigating to a webpage or submitting a form: 2.0 to 3.5
      - Launching or switching heavy desktop applications: 2.5 to 4.0
    </rule>
    <rule name="never_repeat_no_change_actions">
      If an action returns "WARNING: Screen looks IDENTICAL" or appears in [COMPLETED ACTIONS] with [NO_SCREEN_CHANGE - DO NOT REPEAT], the system will block identical retries. Immediately pivot to another element_id, keyboard shortcut, or raw coordinate.
    </rule>
  </wait_and_verify>

  <failure_recovery>
    <rule name="two_strike_rule">
      Strike 1: If an action is rejected or produces no screen change, adjust once (pick the alternative element_id from close matches, target the parent [button] instead of [text], or use press_key 'enter').
      Strike 2: If the second attempt also fails to change the screen, ABANDON that element path immediately. Pivot to a keyboard shortcut, scroll the view, or use raw click coordinates from the screenshot.
    </rule>
    <rule name="popup_and_modal_priority">
      If an observe call reveals a new modal dialog, UAC/confirmation prompt, or error popup (e.g., buttons like "OK", "Cancel", "Yes", "Save", "Don't Save"), handle or dismiss the modal FIRST. Background elements behind a modal cannot receive clicks until the modal is resolved.
    </rule>
  </failure_recovery>

  <scroll_rules>
    - Use action='scroll_down' or 'scroll_up' to reveal off-screen content.
    - When scrolling a specific pane or sidebar (e.g., WhatsApp chat list, VS Code explorer, webpage container), pass element_id (or x,y) of an element inside that pane so the mouse hovers over the correct scrollable area before scrolling.
    - After ANY scroll action, all previous element_ids are invalid — ALWAYS call action='observe' next.
  </scroll_rules>

  <drag_and_drop_rules>
    - When dragging one detected UI element onto another, pass start_element_id and end_element_id from the latest observe list.
    - When dragging across a canvas, slider track, or text selection where start/end points are not discrete elements, pass integer start_x, start_y, end_x, and end_y.
    - Always call action='observe' after drag_and_drop to confirm the new position.
  </drag_and_drop_rules>

  <ambiguity_resolution>
    If two or more elements in the observe list share the same label (e.g., multiple "Close", "Add", or "OK" buttons):
    1. Check the [role] tag — prefer [button] or [menuitem] over [text].
    2. Check the @(x,y) coordinates and match them against the yellow badge numbers in the screenshot image.
    3. Pick the exact #N badge visually located inside the target panel or dialog.
  </ambiguity_resolution>

  <verification_checkpoints>
    Before calling 'complete_task' on any GUI mission, verify via the latest observe screenshot and element list that:
    1. The requested final state is visibly achieved on screen (e.g., calculation result displayed, message sent, file saved, setting toggled).
    2. No unexpected error dialog or unsubmitted form remains open.
  </verification_checkpoints>

  <anti_patterns>
    NEVER do any of these:
    - Click or type by element_id without calling 'observe' first.
    - Reuse an element_id from an earlier step after the UI has changed or scrolled.
    - Omit expected_text when calling click_by_id, double_click_by_id, or right_click_by_id.
    - Retry an action that produced "WARNING: Screen looks IDENTICAL" with the exact same parameters.
    - Waste multiple steps manually clicking, pressing ctrl+a, and pasting when a single action='type' with element_id, expected_text, clear_first=true, and press_enter=true can do it in one step.
    - Call 'complete_task' right after an action without a final 'observe' confirming success.
  </anti_patterns>

</domain_module>"""