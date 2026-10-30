import os
import time
import base64
import tempfile
import ctypes
from ctypes import wintypes
import hashlib
import threading
import difflib
from collections import deque
import pyautogui
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from core.logger.logger import logger

pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0.04

try:
    ctypes.windll.user32.SetProcessDpiAwarenessContext(-4)
except Exception:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

_RESAMPLE_LANCZOS = getattr(Image, "Resampling", Image).LANCZOS
_RESAMPLE_BILINEAR = getattr(Image, "Resampling", Image).BILINEAR

_ocr_engine = None
_ocr_lock = threading.Lock()
_LAST_ELEMENTS = []
_LAST_SCREEN_HASH = None
_CACHE_LOCK = threading.Lock()
_MAX_ELEMENTS = 300
_DEFAULT_POST_ACTION_WAIT = 0.5
_SELF_PID = os.getpid()

_INTERACTIVE_UIA_ROLES = {
    "ButtonControl", "EditControl", "HyperlinkControl", "MenuItemControl",
    "ComboBoxControl", "CheckBoxControl", "RadioButtonControl", "ListItemControl",
    "TabItemControl", "TreeItemControl", "DataItemControl", "SpinnerControl",
    "SliderControl", "SplitButtonControl", "TextControl", "DocumentControl",
}

_PURE_CONTAINER_UIA_ROLES = {
    "WindowControl", "PaneControl", "GroupControl", "ToolBarControl",
    "MenuBarControl", "StatusBarControl", "ScrollBarControl",
    "TitleBarControl", "HeaderControl",
}

_SYMBOL_EQUIVALENTS = {
    "×": "*",
    "✕": "*",
    "✖": "*",
    "÷": "/",
    "−": "-",
    "–": "-",
    "—": "-",
}


class _GUITHREADINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("hwndActive", wintypes.HWND),
        ("hwndFocus", wintypes.HWND),
        ("hwndCapture", wintypes.HWND),
        ("hwndMenuOwner", wintypes.HWND),
        ("hwndMoveSize", wintypes.HWND),
        ("hwndCaret", wintypes.HWND),
        ("rcCaret", wintypes.RECT),
    ]


def _get_gui_focus_signature():
    try:
        fg_hwnd = ctypes.windll.user32.GetForegroundWindow()
        gti = _GUITHREADINFO()
        gti.cbSize = ctypes.sizeof(_GUITHREADINFO)
        if ctypes.windll.user32.GetGUIThreadInfo(0, ctypes.byref(gti)):
            return (
                fg_hwnd,
                gti.hwndActive,
                gti.hwndFocus,
                gti.hwndCapture,
                gti.hwndMenuOwner,
                gti.hwndCaret,
                gti.rcCaret.left,
                gti.rcCaret.top,
                gti.rcCaret.right,
                gti.rcCaret.bottom,
            )
        return (fg_hwnd,)
    except Exception:
        return None


def _get_ocr():
    global _ocr_engine
    if _ocr_engine is not None:
        return _ocr_engine

    with _ocr_lock:
        if _ocr_engine is not None:
            return _ocr_engine

        try:
            from rapidocr_onnxruntime import RapidOCR
            _ocr_engine = RapidOCR()
            logger.info("RapidOCR initialized.")
        except Exception as e:
            logger.warning(f"RapidOCR init failed: {e}")
            _ocr_engine = False

    return _ocr_engine


def warmup_gui_system():
    hr = None
    try:
        hr = ctypes.windll.ole32.CoInitialize(None)
    except Exception:
        pass
    try:
        logger.info("Loading GUI Grounding System (RapidOCR + UIA)...")

        ocr = _get_ocr()
        if ocr:
            dummy = np.zeros((64, 64, 3), dtype=np.uint8)
            try:
                ocr(dummy)
                logger.info("RapidOCR warmup complete.")
            except Exception as e:
                logger.warning(f"OCR warmup inference failed: {e}")

        try:
            import uiautomation
            _ = uiautomation.GetForegroundControl()
            logger.info("UIA warmup complete.")
        except Exception as e:
            logger.warning(f"UIA warmup failed: {e}")

        logger.info("GUI Grounding System ready.")
    except Exception as e:
        logger.warning(f"GUI system warmup failed: {e}")
    finally:
        if hr in (0, 1):
            try:
                ctypes.windll.ole32.CoUninitialize()
            except Exception:
                pass


def _mask_clock_region(img):
    masked = img.copy()
    w, h = masked.size
    draw = ImageDraw.Draw(masked)
    draw.rectangle([max(0, w - 160), max(0, h - 52), w, h], fill=(0, 0, 0))
    return masked


def _compute_screen_hash(img):
    masked = _mask_clock_region(img)
    small = masked.resize((384, 216), _RESAMPLE_BILINEAR)
    quantized = small.point(lambda p: (p // 6) * 6)
    return hashlib.md5(quantized.tobytes()).hexdigest()


def _capture_rgb():
    try:
        return pyautogui.screenshot().convert("RGB")
    except Exception:
        return None


def _has_screen_changed(before_img, after_img, target_xy=None, before_sig=None, after_sig=None):
    if before_sig is not None and after_sig is not None and before_sig != after_sig:
        return True
    if before_img is None or after_img is None:
        return True
    if _compute_screen_hash(before_img) != _compute_screen_hash(after_img):
        return True

    b_masked = np.asarray(_mask_clock_region(before_img), dtype=np.float32)
    a_masked = np.asarray(_mask_clock_region(after_img), dtype=np.float32)
    full_diff = np.abs(b_masked - a_masked)
    if np.mean(full_diff > 18.0) > 0.0004:
        return True

    if target_xy is not None:
        tx, ty = target_xy
        w, h = before_img.size
        x1, y1 = max(0, tx - 160), max(0, ty - 100)
        x2, y2 = min(w, tx + 160), min(h, ty + 100)
        if x2 > x1 and y2 > y1:
            b_arr = np.asarray(before_img.crop((x1, y1, x2, y2)), dtype=np.float32)
            a_arr = np.asarray(after_img.crop((x1, y1, x2, y2)), dtype=np.float32)
            diff = np.abs(b_arr - a_arr)
            if diff.mean() > 1.1 or np.mean(diff > 20.0) > 0.005:
                return True
    return False


def _invalidate_cache():
    global _LAST_SCREEN_HASH
    with _CACHE_LOCK:
        _LAST_SCREEN_HASH = None


def _detect_text_elements(img):
    ocr = _get_ocr()
    if not ocr:
        return []

    orig_w, orig_h = img.size
    scale = 1.0
    work_img = img
    if orig_w > 1920:
        scale = 1920.0 / orig_w
        new_size = (int(orig_w * scale), int(orig_h * scale))
        work_img = img.resize(new_size, _RESAMPLE_LANCZOS)

    arr = np.array(work_img)
    elements = []

    try:
        result, _ = ocr(arr)
        if result:
            for item in result:
                try:
                    box, text, conf = item[0], item[1], float(item[2])
                    clean_text = str(text).strip()
                    if not clean_text:
                        continue
                    min_conf = 0.32 if len(clean_text) <= 2 else 0.45
                    if conf < min_conf:
                        continue
                    xs = [p[0] / scale for p in box]
                    ys = [p[1] / scale for p in box]
                    x1 = max(0, min(orig_w, int(round(min(xs)))))
                    y1 = max(0, min(orig_h, int(round(min(ys)))))
                    x2 = max(0, min(orig_w, int(round(max(xs)))))
                    y2 = max(0, min(orig_h, int(round(max(ys)))))
                    bw = x2 - x1
                    bh = y2 - y1
                    if bw < 3 or bh < 3:
                        continue

                    if "  " in clean_text and bw > bh * 5.0:
                        parts = [p.strip() for p in clean_text.split("  ") if p.strip()]
                        if len(parts) > 1:
                            step_w = bw / len(parts)
                            for idx, part in enumerate(parts):
                                px1 = int(round(x1 + idx * step_w))
                                px2 = int(round(x1 + (idx + 1) * step_w))
                                elements.append({
                                    "text": part,
                                    "bbox": (px1, y1, px2, y2),
                                    "role": "text",
                                    "source": "ocr",
                                })
                            continue

                    elements.append({
                        "text": clean_text,
                        "bbox": (x1, y1, x2, y2),
                        "role": "text",
                        "source": "ocr",
                    })
                except Exception:
                    continue
    except Exception as e:
        logger.warning(f"RapidOCR inference failed: {e}")

    return elements


def _humanize_auto_id(auto_id):
    if not auto_id or auto_id.isdigit():
        return ""
    cleaned = auto_id.replace("_", " ").replace("-", " ")
    out = []
    for ch in cleaned:
        if ch.isupper() and out and out[-1].islower():
            out.append(" ")
        out.append(ch)
    res = "".join(out).strip()
    if len(res) < 2 or res.isdigit():
        return ""
    return res[:50]


def _extract_uia_label(ctrl, role, w, h):
    try:
        name = (ctrl.Name or "").strip()
        if name:
            return name[:80]
    except Exception:
        pass

    try:
        val_pat = ctrl.GetValuePattern()
        if val_pat:
            val = (val_pat.Value or "").strip()
            if val:
                return val[:70]
    except Exception:
        pass

    try:
        legacy = ctrl.GetLegacyIAccessiblePattern()
        if legacy:
            leg_name = (legacy.Name or "").strip()
            if leg_name:
                return leg_name[:70]
            leg_desc = (legacy.Description or "").strip()
            if leg_desc:
                return leg_desc[:70]
    except Exception:
        pass

    try:
        help_text = (ctrl.HelpText or "").strip()
        if help_text:
            return help_text[:70]
    except Exception:
        pass

    if role in ("ButtonControl", "SplitButtonControl", "MenuItemControl", "TabItemControl", "HyperlinkControl", "ListItemControl"):
        try:
            for sub in ctrl.GetChildren()[:4]:
                sub_name = (sub.Name or "").strip()
                if sub_name:
                    return sub_name[:70]
                sub_help = (sub.HelpText or "").strip()
                if sub_help:
                    return sub_help[:70]
        except Exception:
            pass

    try:
        auto_id = (ctrl.AutomationId or "").strip()
    except Exception:
        auto_id = ""

    human_id = _humanize_auto_id(auto_id)

    if role in ("EditControl", "ComboBoxControl", "DocumentControl"):
        if human_id:
            return f"Input ({human_id})"
        return "Input Field"

    if role in ("ButtonControl", "SplitButtonControl", "CheckBoxControl", "RadioButtonControl", "TabItemControl", "MenuItemControl"):
        if human_id:
            return human_id
        if 12 <= w <= 96 and 12 <= h <= 72:
            clean_role = role.replace("Control", "")
            return f"Icon {clean_role}"

    return ""


def _is_point_occluded(cx, cy, occluded_rects):
    for (ox1, oy1, ox2, oy2) in occluded_rects:
        if ox1 <= cx <= ox2 and oy1 <= cy <= oy2:
            return True
    return False


def _scan_window_bfs(win_ctrl, screen_w, screen_h, screen_area, occluded_rects, elements, seen_keys, deadline, max_elements=340):
    queue = deque([(win_ctrl, 0)])
    while queue and len(elements) < max_elements and time.perf_counter() <= deadline:
        ctrl, depth = queue.popleft()
        if depth > 14:
            continue
        try:
            children = ctrl.GetChildren()
        except Exception:
            continue

        for child in children:
            if time.perf_counter() > deadline or len(elements) >= max_elements:
                break
            try:
                try:
                    if child.IsOffscreen:
                        continue
                except Exception:
                    pass

                rect = child.BoundingRectangle
                if not rect:
                    continue

                x1 = max(0, int(rect.left))
                y1 = max(0, int(rect.top))
                x2 = min(screen_w, int(rect.right))
                y2 = min(screen_h, int(rect.bottom))
                w = x2 - x1
                h = y2 - y1

                if w < 5 or h < 5:
                    continue

                role = getattr(child, "ControlTypeName", "") or "Control"
                area = w * h

                if depth < 14:
                    queue.append((child, depth + 1))

                if role in _PURE_CONTAINER_UIA_ROLES:
                    continue

                if role == "DocumentControl" and area > 0.25 * screen_area:
                    continue

                if role == "CustomControl" and area > 0.04 * screen_area:
                    continue

                if role in _INTERACTIVE_UIA_ROLES or (role in ("CustomControl", "ImageControl") and area <= 0.03 * screen_area):
                    cx = (x1 + x2) // 2
                    cy = (y1 + y2) // 2
                    if _is_point_occluded(cx, cy, occluded_rects):
                        continue

                    label = _extract_uia_label(child, role, w, h)
                    if label:
                        clean_role = role.replace("Control", "").lower() or "control"
                        dedup_key = (x1 // 4, y1 // 4, x2 // 4, y2 // 4, label.lower())
                        if dedup_key not in seen_keys:
                            seen_keys.add(dedup_key)
                            elements.append({
                                "text": label,
                                "bbox": (x1, y1, x2, y2),
                                "role": clean_role,
                                "source": "uia",
                            })
            except Exception:
                continue


def _detect_uia_elements(screen_size):
    try:
        import uiautomation as auto
    except ImportError:
        return []

    hr = None
    try:
        hr = ctypes.windll.ole32.CoInitialize(None)
    except Exception:
        pass

    screen_w, screen_h = screen_size
    screen_area = max(1, screen_w * screen_h)
    elements = []
    seen_keys = set()
    occluded_rects = []
    scanned_handles = set()
    deadline = time.perf_counter() + 2.2

    try:
        fg = auto.GetForegroundControl()
        if fg:
            try:
                fg_pid = getattr(fg, "ProcessId", None)
                fg_handle = getattr(fg, "NativeWindowHandle", None)
                fg_rect = fg.BoundingRectangle
            except Exception:
                fg_pid, fg_handle, fg_rect = None, None, None

            if fg_pid != _SELF_PID:
                if fg_handle:
                    scanned_handles.add(fg_handle)
                _scan_window_bfs(
                    fg, screen_w, screen_h, screen_area,
                    occluded_rects, elements, seen_keys, deadline
                )
                if fg_rect:
                    fx1 = max(0, int(fg_rect.left))
                    fy1 = max(0, int(fg_rect.top))
                    fx2 = min(screen_w, int(fg_rect.right))
                    fy2 = min(screen_h, int(fg_rect.bottom))
                    if (fx2 - fx1) > 40 and (fy2 - fy1) > 40:
                        occluded_rects.append((fx1, fy1, fx2, fy2))

        if time.perf_counter() <= deadline:
            root = auto.GetRootControl()
            if root:
                for win in root.GetChildren():
                    if time.perf_counter() > deadline or len(elements) >= 340:
                        break
                    try:
                        win_pid = getattr(win, "ProcessId", None)
                        if win_pid == _SELF_PID:
                            continue
                        win_handle = getattr(win, "NativeWindowHandle", None)
                        if win_handle and win_handle in scanned_handles:
                            continue
                        if getattr(win, "IsOffscreen", False):
                            continue

                        cls_name = (win.ClassName or "").strip()
                        rect = win.BoundingRectangle
                        if not rect:
                            continue

                        wx1 = max(0, int(rect.left))
                        wy1 = max(0, int(rect.top))
                        wx2 = min(screen_w, int(rect.right))
                        wy2 = min(screen_h, int(rect.bottom))
                        ww = wx2 - wx1
                        wh = wy2 - wy1
                        if ww < 30 or wh < 25:
                            continue

                        if cls_name in ("Shell_TrayWnd", "NotifyIconOverflowWindow", "#32768"):
                            _scan_window_bfs(
                                win, screen_w, screen_h, screen_area,
                                [], elements, seen_keys, deadline
                            )
                            continue

                        if cls_name in ("Progman", "WorkerW"):
                            continue

                        _scan_window_bfs(
                            win, screen_w, screen_h, screen_area,
                            occluded_rects, elements, seen_keys, deadline
                        )
                        if win_handle:
                            scanned_handles.add(win_handle)
                        occluded_rects.append((wx1, wy1, wx2, wy2))
                    except Exception:
                        continue
    except Exception as e:
        logger.warning(f"UIA detection failed: {e}")
    finally:
        if hr in (0, 1):
            try:
                ctypes.windll.ole32.CoUninitialize()
            except Exception:
                pass

    return elements


def _merge_elements(text_els, uia_els):
    merged = []
    for u in uia_els:
        ux1, uy1, ux2, uy2 = u["bbox"]
        u_area = max(1, (ux2 - ux1) * (uy2 - uy1))
        u_text = u["text"].strip().lower()
        duplicate_uia = False
        for existing in merged:
            ex1, ey1, ex2, ey2 = existing["bbox"]
            e_area = max(1, (ex2 - ex1) * (ey2 - ey1))
            ix = max(0, min(ux2, ex2) - max(ux1, ex1))
            iy = max(0, min(uy2, ey2) - max(uy1, ey1))
            inter = ix * iy
            if inter > 0.75 * min(u_area, e_area) and u_text == existing["text"].strip().lower():
                duplicate_uia = True
                break
        if not duplicate_uia:
            merged.append(dict(u))

    for t in text_els:
        tx1, ty1, tx2, ty2 = t["bbox"]
        t_area = max(1, (tx2 - tx1) * (ty2 - ty1))
        tcx = (tx1 + tx2) * 0.5
        tcy = (ty1 + ty2) * 0.5
        dup = False

        for u in merged:
            ux1, uy1, ux2, uy2 = u["bbox"]
            u_area = max(1, (ux2 - ux1) * (uy2 - uy1))
            if u_area > 6 * t_area:
                continue

            ix = max(0, min(tx2, ux2) - max(tx1, ux1))
            iy = max(0, min(ty2, uy2) - max(ty1, uy1))
            inter = ix * iy
            center_inside = (ux1 <= tcx <= ux2) and (uy1 <= tcy <= uy2)

            if inter > 0.45 * min(t_area, u_area) or (center_inside and u_area <= 4.5 * t_area):
                if u["text"].startswith("Input") and t["text"] and t["text"].lower() not in u["text"].lower():
                    u["text"] = f"{u['text']}: {t['text']}"
                elif u["text"].startswith("Icon ") and t["text"]:
                    u["text"] = t["text"]
                dup = True
                break

        if not dup:
            merged.append(dict(t))

    if len(merged) > _MAX_ELEMENTS:
        interactive_priority = [
            e for e in merged
            if e.get("source") == "uia" and e.get("role") not in ("text", "control")
        ]
        others = [
            e for e in merged
            if not (e.get("source") == "uia" and e.get("role") not in ("text", "control"))
        ]
        merged = (interactive_priority + others)[:_MAX_ELEMENTS]

    merged.sort(key=lambda e: (e["bbox"][1] // 18, e["bbox"][0]))
    return merged[:_MAX_ELEMENTS]


def _load_font(size):
    candidates = [
        "arialbd.ttf", "segoeuib.ttf", "calibrib.ttf", "tahomabd.ttf",
        "arial.ttf", "segoeui.ttf", "calibri.ttf", "tahoma.ttf", "verdana.ttf",
    ]
    for name in candidates:
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            continue
    return ImageFont.load_default()


def _draw_marks(img, elements):
    marked = img.copy().convert("RGB")
    draw = ImageDraw.Draw(marked, "RGBA")
    font_small = _load_font(11)
    w, h = marked.size
    placed_badges = []
    centers = [_element_center(el) for el in elements]

    for i, el in enumerate(elements, start=1):
        x1, y1, x2, y2 = el["bbox"]
        pad = 1
        rx1, ry1 = max(0, x1 - pad), max(0, y1 - pad)
        rx2, ry2 = min(w - 1, x2 + pad), min(h - 1, y2 + pad)

        outline_color = (255, 0, 0, 235) if el.get("source") == "ocr" else (0, 170, 255, 235)
        draw.rectangle([rx1, ry1, rx2, ry2], outline=outline_color, width=2)

        badge = str(i)
        try:
            bbox = draw.textbbox((0, 0), badge, font=font_small)
            bw = bbox[2] - bbox[0]
            bh = bbox[3] - bbox[1]
        except Exception:
            bw, bh = 10, 11

        pad_w = bw + 6
        pad_h = bh + 4

        candidates = [
            (rx1, max(0, ry1 - pad_h - 1)),
            (max(0, rx1 - pad_w - 1), ry1),
            (rx1, min(h - pad_h, ry2 + 1)),
            (min(w - pad_w, rx2 - pad_w), max(0, ry1 - pad_h - 1)),
            (rx1 + 1, ry1 + 1),
        ]

        bx1, by1 = candidates[0]
        for cx, cy in candidates:
            cbx2 = min(w, cx + pad_w)
            cby2 = min(h, cy + pad_h)
            badge_overlap = any(
                not (cbx2 < px1 or cx > px2 or cby2 < py1 or cy > py2)
                for (px1, py1, px2, py2) in placed_badges
            )
            center_overlap = any(
                idx != (i - 1) and (cx <= ecx <= cbx2 and cy <= ecy <= cby2)
                for idx, (ecx, ecy) in enumerate(centers)
            )
            if not badge_overlap and not center_overlap:
                bx1, by1 = cx, cy
                break

        bx2 = min(w, bx1 + pad_w)
        by2 = min(h, by1 + pad_h)
        placed_badges.append((bx1, by1, bx2, by2))

        draw.rectangle([bx1, by1, bx2, by2], fill=(255, 220, 0, 225), outline=(0, 0, 0, 255), width=1)
        draw.text((bx1 + 3, by1 + 1), badge, fill=(0, 0, 0, 255), font=font_small)

    return marked


def _capture_grounded_screen():
    global _LAST_ELEMENTS, _LAST_SCREEN_HASH

    screenshot = pyautogui.screenshot().convert("RGB")
    curr_hash = _compute_screen_hash(screenshot)
    out_path = os.path.join(tempfile.gettempdir(), "jarvis_som_screen.png")

    with _CACHE_LOCK:
        if curr_hash == _LAST_SCREEN_HASH and _LAST_ELEMENTS:
            logger.info(f"Screen unchanged - reusing {len(_LAST_ELEMENTS)} cached elements.")
            marked = _draw_marks(screenshot, _LAST_ELEMENTS)
            marked.save(out_path)
            return out_path, list(_LAST_ELEMENTS), screenshot.size

    uia_els = _detect_uia_elements(screenshot.size)
    text_els = _detect_text_elements(screenshot)
    elements = _merge_elements(text_els, uia_els)

    logger.info(
        f"Hybrid UIA+OCR detection: {len(uia_els)} UIA + {len(text_els)} OCR "
        f"= {len(elements)} merged elements."
    )

    with _CACHE_LOCK:
        _LAST_ELEMENTS = elements
        _LAST_SCREEN_HASH = curr_hash

    marked = _draw_marks(screenshot, elements)
    marked.save(out_path)

    return out_path, list(elements), screenshot.size


def _element_center(el):
    x1, y1, x2, y2 = el["bbox"]
    return (x1 + x2) // 2, (y1 + y2) // 2


def _resolve_target_coords(gui_cmd, action_name):
    x = gui_cmd.get("x")
    y = gui_cmd.get("y")
    eid = gui_cmd.get("element_id")

    if (x is None or y is None) and eid is not None:
        try:
            eid = int(eid)
        except (ValueError, TypeError):
            eid = None
        if eid is not None and 1 <= eid <= len(_LAST_ELEMENTS):
            el = _LAST_ELEMENTS[eid - 1]
            x, y = _element_center(el)
            logger.info(f"Resolved element_id #{eid} '{el['text'][:40]}' -> ({x},{y})")
        else:
            return None, None, (
                f"Observation: Error -> element_id {eid} invalid. "
                f"Valid range 1-{len(_LAST_ELEMENTS)}. Call 'observe' first."
            )

    if x is None or y is None:
        return None, None, (
            f"Observation: Error -> action='{action_name}' requires either 'element_id' "
            f"OR both 'x' and 'y' as integers."
        )

    try:
        return int(x), int(y), None
    except (ValueError, TypeError):
        return None, None, "Observation: Error -> 'x' and 'y' must be valid integers."


def _normalize_label(s):
    text = str(s or "").strip().lower()
    for k, v in _SYMBOL_EQUIVALENTS.items():
        text = text.replace(k, v)
    return " ".join(text.split())


def _tokenize_label(s):
    cleaned = s
    for ch in "[]():,._\"'|":
        cleaned = cleaned.replace(ch, " ")
    return [tok for tok in cleaned.split() if tok]


def _verify_expected_text(eid, expected_text):
    el = _LAST_ELEMENTS[eid - 1]
    actual = _normalize_label(el["text"])
    expected_lower = _normalize_label(expected_text)

    if not expected_lower:
        return None

    if expected_lower == actual:
        return None

    actual_tokens = _tokenize_label(actual)
    expected_tokens = _tokenize_label(expected_lower)

    if len(expected_lower) <= 2:
        if expected_lower in actual_tokens or (expected_tokens and expected_tokens[0] in actual_tokens):
            return None
        if actual.startswith(expected_lower) or actual.endswith(expected_lower):
            return None
    else:
        if expected_lower in actual or actual in expected_lower:
            return None
        if expected_tokens and actual_tokens and set(expected_tokens).issubset(set(actual_tokens)):
            return None
        if difflib.SequenceMatcher(None, expected_lower, actual).ratio() >= 0.80:
            return None

    candidates = []
    for idx, e in enumerate(_LAST_ELEMENTS, 1):
        e_text = _normalize_label(e["text"])
        if not e_text:
            continue
        e_tokens = _tokenize_label(e_text)
        is_match = False
        if len(expected_lower) <= 2:
            is_match = (
                expected_lower == e_text
                or expected_lower in e_tokens
                or (expected_tokens and expected_tokens[0] in e_tokens)
            )
        else:
            is_match = (
                expected_lower in e_text
                or e_text in expected_lower
                or difflib.SequenceMatcher(None, expected_lower, e_text).ratio() >= 0.78
            )
        if is_match:
            cx, cy = _element_center(e)
            candidates.append(f"#{idx} [{e.get('role', 'el')}] '{e['text'][:40]}' @({cx},{cy})")
            if len(candidates) >= 6:
                break

    hint = (
        f" Close matches: {', '.join(candidates)}."
        if candidates
        else " No close matches found."
    )

    return (
        f"Observation: Error -> element_id #{eid} actual text is '{el['text'][:50]}' "
        f"but you expected '{expected_text}'. CLICK REJECTED to prevent wrong action."
        f"{hint} Pick the correct element_id from the latest 'observe' list."
    )


def handle_gui_controller(gui_cmd: dict):
    global _LAST_ELEMENTS
    try:
        action = gui_cmd.get("action")
        wait_time = float(gui_cmd.get("wait_after_action", 0.0) or 0.0)

        if action == "observe":
            filepath, elements, (w, h) = _capture_grounded_screen()
            _LAST_ELEMENTS = elements

            with open(filepath, "rb") as f:
                img_b64 = base64.b64encode(f.read()).decode("utf-8")

            elem_lines = []
            for i, el in enumerate(elements, 1):
                cx, cy = _element_center(el)
                role = el.get("role", "text")
                text_preview = el["text"][:70].replace("\n", " ")
                elem_lines.append(f"#{i} [{role}] \"{text_preview}\" @({cx},{cy})")

            listing = (
                "\n".join(elem_lines[:_MAX_ELEMENTS])
                if elem_lines
                else "(no elements detected)"
            )

            return {
                "type": "image_payload",
                "data": [{"mime_type": "image/png", "data": img_b64}],
                "observation": (
                    f"Observation: Screen captured ({w}x{h}). "
                    f"{len(elements)} interactive elements detected and numbered "
                    f"(highlighted boxes with yellow badges).\n\n"
                    f"DETECTED ELEMENTS:\n{listing}\n\n"
                    f"WORKFLOW:\n"
                    f"1. To click any element above, call gui_controller with "
                    f"action='click_by_id', element_id=<number>, AND "
                    f"expected_text='<exact label from list>'.\n"
                    f"2. The system verifies expected_text against the actual element label. "
                    f"If they do not match, the click is REJECTED.\n"
                    f"3. For double-click use 'double_click_by_id', for right-click use "
                    f"'right_click_by_id' — always with expected_text.\n"
                    f"4. Only use raw 'click' with x,y coordinates if the target is NOT "
                    f"in the list above (e.g., canvas, image area, custom drawing).\n"
                    f"5. To type text, use action='type' with 'text' parameter, optionally "
                    f"including 'element_id', 'expected_text', 'clear_first', and 'press_enter'.\n"
                    f"6. After every action, call 'observe' again to verify the UI changed "
                    f"correctly.\n"
                    f"7. IMPORTANT: If a click response warns that the screen did NOT "
                    f"change, do NOT retry the same element_id or coordinates. Try pressing "
                    f"'enter'/'tab', or observe again and pick a different element."
                ),
            }

        if action in ["click_by_id", "double_click_by_id", "right_click_by_id"]:
            eid = gui_cmd.get("element_id")
            if eid is None:
                return "Observation: Error -> 'element_id' is required for click_by_id actions."
            try:
                eid = int(eid)
            except (ValueError, TypeError):
                return "Observation: Error -> 'element_id' must be an integer."
            if eid < 1 or eid > len(_LAST_ELEMENTS):
                return (
                    f"Observation: Error -> element_id {eid} out of range. "
                    f"Valid range: 1-{len(_LAST_ELEMENTS)}. Call 'observe' first."
                )

            expected_text = str(gui_cmd.get("expected_text", "") or "").strip()
            if not expected_text:
                return (
                    f"Observation: Error -> 'expected_text' is mandatory when calling {action}. "
                    f"Element #{eid} has label '{_LAST_ELEMENTS[eid - 1]['text'][:50]}'. "
                    f"Pass expected_text with the exact label to confirm."
                )

            verify_err = _verify_expected_text(eid, expected_text)
            if verify_err:
                logger.warning(
                    f"Element verification failed for #{eid}. "
                    f"Expected '{expected_text}'."
                )
                return verify_err

            el = _LAST_ELEMENTS[eid - 1]
            x, y = _element_center(el)

            logger.info(f"{action} -> element #{eid} '{el['text'][:50]}' at ({x},{y})")

            before_sig = _get_gui_focus_signature()
            pyautogui.moveTo(x, y, duration=0.10)
            time.sleep(0.04)
            before_img = _capture_rgb()

            if action == "click_by_id":
                pyautogui.click()
            elif action == "double_click_by_id":
                pyautogui.doubleClick()
            elif action == "right_click_by_id":
                pyautogui.rightClick()

            _invalidate_cache()

            sleep_for = wait_time if wait_time > 0 else _DEFAULT_POST_ACTION_WAIT
            time.sleep(sleep_for)

            after_img = _capture_rgb()
            after_sig = _get_gui_focus_signature()
            warning = ""
            if not _has_screen_changed(before_img, after_img, (x, y), before_sig, after_sig):
                logger.warning(
                    f"Click on #{eid} produced no visible change."
                )
                warning = (
                    f" WARNING: Screen looks IDENTICAL after the click. The action may "
                    f"not have registered, the element may be non-interactive, or a popup "
                    f"may be blocking. DO NOT repeat the same element_id or coordinates. "
                    f"Options: (a) press 'enter' or 'tab' via 'press_key', "
                    f"(b) call 'observe' and pick a different element, "
                    f"(c) use raw 'click' with different coordinates."
                )

            return (
                f"Observation: Performed {action} on element #{eid} "
                f"'{el['text'][:50]}' at ({x},{y}).{warning} "
                f"Call 'observe' to verify the result."
            )

        if action == "type":
            text = str(gui_cmd.get("text", "") or "")
            if not text:
                return "Observation: Error -> 'type' action requires a non-empty 'text' parameter."

            x = gui_cmd.get("x")
            y = gui_cmd.get("y")
            eid = gui_cmd.get("element_id")
            clear_first = bool(gui_cmd.get("clear_first", False))
            press_enter = bool(gui_cmd.get("press_enter", False))

            if (x is None or y is None) and eid is not None:
                try:
                    eid = int(eid)
                except (ValueError, TypeError):
                    eid = None
                if eid is not None and 1 <= eid <= len(_LAST_ELEMENTS):
                    expected_text = gui_cmd.get("expected_text", "")
                    if expected_text:
                        verify_err = _verify_expected_text(eid, str(expected_text))
                        if verify_err:
                            return verify_err
                    el = _LAST_ELEMENTS[eid - 1]
                    x, y = _element_center(el)
                    logger.info(
                        f"Type target resolved from element_id #{eid} -> ({x},{y})"
                    )
                else:
                    return (
                        f"Observation: Error -> element_id {eid} invalid. "
                        f"Valid range 1-{len(_LAST_ELEMENTS)}. Call 'observe' first."
                    )

            before_sig = _get_gui_focus_signature()
            before_img = _capture_rgb()
            target_xy = None

            if x is not None and y is not None:
                try:
                    x, y = int(x), int(y)
                except (ValueError, TypeError):
                    return "Observation: Error -> 'x' and 'y' must be valid integers."
                target_xy = (x, y)
                logger.info(f"Clicking at ({x},{y}) before typing.")
                pyautogui.moveTo(x, y, duration=0.10)
                pyautogui.click()
                time.sleep(0.18)
            else:
                logger.info("Typing directly into currently focused element.")

            if clear_first:
                pyautogui.hotkey("ctrl", "a")
                time.sleep(0.05)
                pyautogui.press("backspace")
                time.sleep(0.06)

            needs_clipboard = (
                not text.isascii()
                or "\n" in text
                or "\t" in text
                or len(text) > 35
                or any(ch in text for ch in "{}[]()\"'`")
            )

            if not needs_clipboard:
                pyautogui.write(text, interval=0.02)
            else:
                pasted = False
                try:
                    from tools.SystemTools.clipboard_tool import write_clipboard
                    if write_clipboard(text):
                        time.sleep(0.04)
                        pyautogui.hotkey("ctrl", "v")
                        pasted = True
                except Exception:
                    pasted = False
                if not pasted:
                    pyautogui.write(text, interval=0.02)

            if press_enter:
                time.sleep(0.08)
                pyautogui.press("enter")

            _invalidate_cache()

            sleep_for = wait_time if wait_time > 0 else 0.35
            time.sleep(sleep_for)

            after_img = _capture_rgb()
            after_sig = _get_gui_focus_signature()
            warning = ""
            if not _has_screen_changed(before_img, after_img, target_xy, before_sig, after_sig):
                logger.warning("Type action produced no visible change on screen.")
                warning = (
                    " WARNING: Screen looks IDENTICAL after typing. The text may not have "
                    "been accepted (field not focused, disabled, or the target moved). "
                    "Call 'observe' and try clicking the field again, or use 'press_key' "
                    "with 'tab' to move focus."
                )

            return (
                f"Observation: Successfully typed {len(text)} characters.{warning}"
            )

        if action in ["click", "left_click", "right_click", "double_click", "hover"]:
            x, y, err = _resolve_target_coords(gui_cmd, action)
            if err:
                return err

            logger.info(f"Executing {action} at ({x},{y})")

            before_sig = _get_gui_focus_signature()
            pyautogui.moveTo(x, y, duration=0.12)

            if action == "hover":
                _invalidate_cache()
                if wait_time > 0:
                    time.sleep(wait_time)
                return f"Observation: Successfully hovered at ({x},{y})."

            time.sleep(0.04)
            before_img = _capture_rgb()

            if action in ["click", "left_click"]:
                pyautogui.click()
            elif action == "right_click":
                pyautogui.rightClick()
            elif action == "double_click":
                pyautogui.doubleClick()

            _invalidate_cache()

            sleep_for = wait_time if wait_time > 0 else _DEFAULT_POST_ACTION_WAIT
            time.sleep(sleep_for)

            after_img = _capture_rgb()
            after_sig = _get_gui_focus_signature()
            warning = ""
            if not _has_screen_changed(before_img, after_img, (x, y), before_sig, after_sig):
                logger.warning(
                    f"Raw {action} at ({x},{y}) produced no visible change."
                )
                warning = (
                    f" WARNING: Screen looks IDENTICAL after this {action}. The target "
                    f"may not be interactive or the coordinates may be off. Try a "
                    f"different approach — call 'observe' and use 'click_by_id' with "
                    f"element_id + expected_text if the target appears in the list."
                )

            return (
                f"Observation: Performed {action} at ({x},{y}).{warning}"
            )

        elif action == "drag_and_drop":
            sx = gui_cmd.get("start_x")
            sy = gui_cmd.get("start_y")
            ex = gui_cmd.get("end_x")
            ey = gui_cmd.get("end_y")
            start_eid = gui_cmd.get("start_element_id")
            end_eid = gui_cmd.get("end_element_id")

            if (sx is None or sy is None) and start_eid is not None:
                try:
                    s_idx = int(start_eid)
                    if 1 <= s_idx <= len(_LAST_ELEMENTS):
                        sx, sy = _element_center(_LAST_ELEMENTS[s_idx - 1])
                    else:
                        return f"Observation: Error -> start_element_id {start_eid} out of range (1-{len(_LAST_ELEMENTS)})."
                except (ValueError, TypeError):
                    return "Observation: Error -> start_element_id must be an integer."

            if (ex is None or ey is None) and end_eid is not None:
                try:
                    e_idx = int(end_eid)
                    if 1 <= e_idx <= len(_LAST_ELEMENTS):
                        ex, ey = _element_center(_LAST_ELEMENTS[e_idx - 1])
                    else:
                        return f"Observation: Error -> end_element_id {end_eid} out of range (1-{len(_LAST_ELEMENTS)})."
                except (ValueError, TypeError):
                    return "Observation: Error -> end_element_id must be an integer."

            if None in [sx, sy, ex, ey]:
                return (
                    "Observation: Error -> drag_and_drop requires either "
                    "(start_element_id and end_element_id) OR (start_x, start_y, end_x, end_y)."
                )

            try:
                sx, sy, ex, ey = int(sx), int(sy), int(ex), int(ey)
            except (ValueError, TypeError):
                return "Observation: Error -> Coordinates must be valid integers."

            logger.info(f"Dragging from ({sx},{sy}) to ({ex},{ey})")
            pyautogui.moveTo(sx, sy, duration=0.15)
            pyautogui.dragTo(ex, ey, duration=0.45, button="left")

            _invalidate_cache()

            if wait_time > 0:
                time.sleep(wait_time)

            return (
                f"Observation: Successfully dragged from ({sx},{sy}) to ({ex},{ey}). "
                f"Call 'observe' to verify the result."
            )

        elif action == "press_key":
            key = gui_cmd.get("key")
            if not key:
                return "Observation: Error -> 'press_key' requires a 'key' parameter."

            logger.info(f"Pressing key: {key}")

            before_sig = _get_gui_focus_signature()
            before_img = _capture_rgb()

            pyautogui.press(str(key))

            _invalidate_cache()

            sleep_for = wait_time if wait_time > 0 else 0.4
            time.sleep(sleep_for)

            after_img = _capture_rgb()
            after_sig = _get_gui_focus_signature()
            warning = ""
            if not _has_screen_changed(before_img, after_img, None, before_sig, after_sig):
                logger.info(f"Key '{key}' produced no visible screen change.")
                warning = (
                    f" NOTE: Screen looks IDENTICAL after pressing '{key}'. If you "
                    f"expected a form submission or navigation, it may not have "
                    f"registered. Try a different key or click the target directly."
                )

            return f"Observation: Pressed key '{key}'.{warning}"

        elif action == "hotkey":
            keys = gui_cmd.get("keys", [])
            if not isinstance(keys, list) or not keys:
                return "Observation: Error -> 'keys' array is missing or empty."

            clean_keys = [str(k) for k in keys if k]
            if not clean_keys:
                return "Observation: Error -> 'keys' array is empty."

            logger.info(f"Pressing hotkey: {clean_keys}")

            before_sig = _get_gui_focus_signature()
            before_img = _capture_rgb()

            pyautogui.hotkey(*clean_keys)

            _invalidate_cache()

            sleep_for = wait_time if wait_time > 0 else 0.4
            time.sleep(sleep_for)

            after_img = _capture_rgb()
            after_sig = _get_gui_focus_signature()
            warning = ""
            if not _has_screen_changed(before_img, after_img, None, before_sig, after_sig):
                logger.info(f"Hotkey {clean_keys} produced no visible screen change.")
                warning = (
                    f" NOTE: Screen looks IDENTICAL after hotkey {clean_keys}. If a UI change "
                    f"was expected, verify the target window is focused."
                )

            return f"Observation: Pressed hotkey combo {clean_keys}.{warning}"

        elif action in ["scroll_down", "scroll_up"]:
            amount = -500 if action == "scroll_down" else 500
            logger.info(f"Scrolling screen: {action}")

            x = gui_cmd.get("x")
            y = gui_cmd.get("y")
            eid = gui_cmd.get("element_id")
            if (x is None or y is None) and eid is not None:
                try:
                    eid_int = int(eid)
                    if 1 <= eid_int <= len(_LAST_ELEMENTS):
                        x, y = _element_center(_LAST_ELEMENTS[eid_int - 1])
                except (ValueError, TypeError):
                    pass

            if x is not None and y is not None:
                try:
                    pyautogui.moveTo(int(x), int(y), duration=0.12)
                except (ValueError, TypeError):
                    pass

            pyautogui.scroll(amount)

            _invalidate_cache()

            if wait_time > 0:
                time.sleep(wait_time)
            else:
                time.sleep(0.3)

            return (
                "Observation: Scrolled screen. You MUST call 'observe' again to see "
                "the updated UI."
            )

        return f"Observation: Unknown GUI action '{action}' requested."

    except pyautogui.FailSafeException:
        logger.warning("PyAutoGUI FailSafe Triggered.")
        return (
            "Observation: Error -> Action aborted. Mouse pushed to corner "
            "triggering FailSafe."
        )
    except Exception as e:
        logger.error(f"Failed to process GUI action: {e}")
        return f"Observation: Error executing GUI action -> {e}"