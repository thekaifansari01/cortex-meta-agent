import os
import re
import json
import base64
import py_compile
from core.logger.logger import logger


class JarvisFileEditor:
    def __init__(self, workspace_dir=None):
        self.workspace = workspace_dir or os.getcwd()
        self.MAX_FILE_SIZE = 1024 * 1024
        self.MAX_CREATE_MANY_FILES = 10
        self.MAX_REPO_MAP_DEPTH = 6
        self.MAX_REPO_MAP_FILES = 200
        self.MAX_VIEW_CHARS = 15000
        self.MAX_BATCH_VIEW_CHARS = 30000
        logger.debug(f"FileEditor initialized with workspace: {self.workspace}")

    def _validate_syntax(self, file_path):
        lower = file_path.lower()
        if lower.endswith(".py"):
            try:
                py_compile.compile(file_path, cfile=os.devnull, doraise=True)
                return " [Syntax Check: OK]"
            except py_compile.PyCompileError as e:
                return f"\n[CRITICAL SYNTAX ERROR DETECTED AFTER EDIT]:\n{e.msg}"
            except Exception as e:
                return f"\n[LINTER WARNING]: {str(e)}"
        if lower.endswith(".json"):
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    json.load(f)
                return " [JSON Check: OK]"
            except json.JSONDecodeError as e:
                return f"\n[CRITICAL JSON ERROR DETECTED AFTER EDIT]:\n{e}"
            except Exception as e:
                return f"\n[JSON LINT WARNING]: {str(e)}"
        return ""

    def _normalize_block(self, block):
        if not block:
            return block
        lines = block.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        while lines and not lines[0].strip():
            lines.pop(0)
        while lines and not lines[-1].strip():
            lines.pop()
        return "\n".join(lines)

    def _Strip_line_numbers_if_copied(self, block):
        if not block:
            return block
        lines = block.split("\n")
        pattern = re.compile(r"^\s*\d+\s*\|\s?(.*)$")
        matched = [pattern.match(line) for line in lines if line.strip()]
        if matched and all(m is not None for m in matched):
            cleaned = []
            for line in lines:
                m = pattern.match(line)
                cleaned.append(m.group(1) if m else line)
            return "\n".join(cleaned)
        return block

    def _truncate_text(self, text, max_chars, label="Content"):
        if len(text) <= max_chars:
            return text
        half = max_chars // 2
        return f"{text[:half]}\n[... TRUNCATED: {len(text) - max_chars} chars ...]\n{text[-half:]}"

    def _read_preserve_newline(self, file_path):
        with open(file_path, "rb") as f:
            raw = f.read()
        has_crlf = b"\r\n" in raw
        text = raw.decode("utf-8", errors="replace")
        normalized = text.replace("\r\n", "\n").replace("\r", "\n")
        return normalized, has_crlf

    def _write_preserve_newline(self, file_path, content, use_crlf):
        if use_crlf:
            output = content.replace("\n", "\r\n")
            with open(file_path, "w", encoding="utf-8", newline="") as f:
                f.write(output)
        else:
            with open(file_path, "w", encoding="utf-8", newline="\n") as f:
                f.write(content)

    def get_repo_map(self, file_path=None, max_files=None, max_depth=None):
        logger.info(f"📁 Generating repo map for path: {file_path or self.workspace}")
        max_files = max_files or self.MAX_REPO_MAP_FILES
        max_depth = max_depth or self.MAX_REPO_MAP_DEPTH
        try:
            target_path = file_path if file_path else self.workspace
            if not os.path.exists(target_path):
                return f"[ERROR] Path '{target_path}' does not exist. Please provide a valid absolute path."
            if not os.path.isdir(target_path):
                return f"[ERROR] '{target_path}' is a file, not a folder. Please provide a valid directory path."
            tree_lines = [f"Project Architecture (Root: {target_path}):"]
            tree_lines.append(f"Max Depth: {max_depth}, Max Files: {max_files}")
            tree_lines.append("")
            count = 0
            skipped_dirs = [
                ".venv", "venv", "env", ".env", "ENV", "virtualenv",
                "node_modules", "bower_components", "jspm_packages",
                "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox",
                ".git", ".svn", ".hg", ".bzr",
                ".idea", ".vscode", ".vs", ".settings", ".metadata",
                "dist", "build", "target", "out", "bin", "obj", "release", "debug",
                ".next", ".nuxt", ".svelte-kit", ".angular", ".parcel-cache", ".turbo",
                "coverage", ".coverage", "htmlcov", ".nyc_output",
                ".cache", ".npm", ".yarn", ".pnpm-store", ".gradle", ".m2",
                "eggs", ".eggs", "wheels",
                "logs", "log", "tmp", "temp", ".tmp", ".temp",
                "site-packages", "lib64", "include", "share",
                "Pods", "Carthage", "DerivedData",
                ".terraform", ".serverless", ".aws-sam",
                "vendor", "Godeps",
                ".history", ".sass-cache", ".eslintcache", ".stylelintcache",
            ]
            skip_extensions = [
                ".pyc", ".pyo", ".pyd", ".pyw",
                ".so", ".dll", ".dylib", ".a", ".lib", ".o", ".obj",
                ".exe", ".msi", ".app", ".dmg", ".deb", ".rpm", ".apk", ".ipa",
                ".class", ".jar", ".war", ".ear", ".aar",
                ".log", ".logs", ".tmp", ".temp", ".cache", ".bak", ".backup", ".old", ".orig",
                ".lock", ".lockfile",
                ".swp", ".swo", ".swn", ".swx",
                ".pid", ".sock", ".seed",
                ".map",
                ".woff", ".woff2", ".ttf", ".eot", ".otf",
                ".ico", ".icns",
                ".mp3", ".mp4", ".avi", ".mov", ".mkv", ".wav", ".flac", ".webm",
                ".zip", ".tar", ".gz", ".bz2", ".xz", ".7z", ".rar", ".tgz",
                ".iso", ".img", ".bin", ".dat", ".db", ".sqlite", ".sqlite3",
                ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
                ".psd", ".ai", ".sketch", ".fig", ".xd",
                ".blend", ".fbx", ".obj3d", ".stl", ".gltf", ".glb",
                ".wasm", ".bc", ".ll",
                ".nupkg", ".snupkg", ".vsix",
                ".pdb", ".ilk", ".exp",
                ".rlib", ".rmeta", ".d",
                ".prof", ".gcda", ".gcno",
                ".heapsnapshot", ".cpuprofile",
                ".dmp", ".crash",
                ".lnk", ".url",
                ".nib", ".xib", ".storyboardc",
                ".pyc.orig", ".rej",
            ]
            blocked_filenames = {
                ".DS_Store", "Thumbs.db", "desktop.ini",
                ".env", ".env.local", ".env.production", ".env.development",
                ".pid.lock", ".npmrc.lock",
            }

            def walk_error(err):
                logger.warning(f"Cannot access: {err.filename} - {err.strerror}")

            for root, dirs, files in os.walk(target_path, onerror=walk_error, followlinks=False):
                rel_root = os.path.relpath(root, target_path)
                depth = 0 if rel_root == '.' else rel_root.count(os.sep) + 1
                if depth > max_depth:
                    dirs.clear()
                    continue
                dirs[:] = [d for d in dirs if d not in skipped_dirs and not d.endswith(".egg-info")]
                dirs.sort()
                files.sort()
                for file in files:
                    if file in blocked_filenames:
                        continue
                    try:
                        file_path_full = os.path.join(root, file)
                        if os.path.getsize(file_path_full) > self.MAX_FILE_SIZE:
                            continue
                    except (OSError, FileNotFoundError):
                        continue
                    ext = os.path.splitext(file)[1].lower()
                    if ext in skip_extensions:
                        continue
                    rel_file = os.path.relpath(os.path.join(root, file), target_path)
                    clean_path = rel_file.replace("\\", "/")
                    tree_lines.append(f"  - {clean_path}")
                    count += 1
                    if count >= max_files:
                        tree_lines.append(f"  ... (TRUNCATED: showing first {max_files} files.)")
                        return "\n".join(tree_lines)
            tree_lines.append("")
            tree_lines.append(f"[SUCCESS] Found {count} files.")
            return "\n".join(tree_lines)
        except Exception as e:
            logger.error(f"Repo map error: {e}")
            return f"[ERROR] Could not generate repo map: {e}"

    def replace_block(self, file_path, search_block, replace_block):
        logger.info(f"🔧 Replacing block in file: {file_path}")
        try:
            if not os.path.exists(file_path):
                return f"[ERROR] File not found: '{file_path}'"

            clean_search_input = self._Strip_line_numbers_if_copied(search_block or "")
            clean_replace_input = self._Strip_line_numbers_if_copied(replace_block or "")

            norm_search = self._normalize_block(clean_search_input)
            norm_replace = self._normalize_block(clean_replace_input)
            if not norm_search:
                return "[ERROR] Search block cannot be empty."

            content, use_crlf = self._read_preserve_newline(file_path)

            matches = content.count(norm_search)
            if matches == 0:
                alt = norm_search.rstrip("\n")
                if alt and content.count(alt) > 0:
                    matches = content.count(alt)
                    norm_search = alt
                else:
                    raw_norm = clean_search_input.replace("\r\n", "\n").replace("\r", "\n")
                    if raw_norm != norm_search and content.count(raw_norm) > 0:
                        matches = content.count(raw_norm)
                        norm_search = raw_norm
                    else:
                        content_lines = content.split("\n")
                        search_lines = norm_search.split("\n")
                        s_len = len(search_lines)
                        stripped_search = [ln.rstrip() for ln in search_lines]
                        found_indices = []
                        for i in range(len(content_lines) - s_len + 1):
                            window = [ln.rstrip() for ln in content_lines[i:i + s_len]]
                            if window == stripped_search:
                                found_indices.append(i)
                        if len(found_indices) == 1:
                            idx = found_indices[0]
                            norm_search = "\n".join(content_lines[idx:idx + s_len])
                            matches = 1
                        elif len(found_indices) > 1:
                            return f"[ERROR] Search block found {len(found_indices)} times in '{file_path}'. Please provide a larger, more unique search block."
                        else:
                            return f"[ERROR] Search block not found in '{file_path}'. Ensure indentation and lines match the current file content."

            if matches == 0:
                return f"[ERROR] Search block not found in '{file_path}'."
            if matches > 1:
                return f"[ERROR] Search block found {matches} times in '{file_path}'. Please provide a larger, more unique search block."

            replace_pos = content.find(norm_search)
            start_line_num = content[:replace_pos].count("\n") + 1
            new_content = content.replace(norm_search, norm_replace, 1)
            self._write_preserve_newline(file_path, new_content, use_crlf)

            new_lines = new_content.split("\n")
            rep_line_count = norm_replace.count("\n") + 1
            ctx_start = max(0, start_line_num - 3)
            ctx_end = min(len(new_lines), start_line_num - 1 + rep_line_count + 2)
            preview_lines = [
                f"{idx + 1}: {new_lines[idx]}"
                for idx in range(ctx_start, ctx_end)
            ]
            preview_snippet = "\n".join(preview_lines)

            syntax_status = self._validate_syntax(file_path)
            if "CRITICAL" in syntax_status:
                logger.error(f"Syntax error after replace in {file_path}: {syntax_status}")
                return (
                    f"[ERROR] Block replaced in '{file_path}' around line {start_line_num}, "
                    f"but broke syntax!{syntax_status}\n\nUpdated Region:\n{preview_snippet}"
                )
            logger.info(f"✅ Block replaced successfully in {file_path}")
            return (
                f"[SUCCESS] Block replaced cleanly in '{file_path}' (around line {start_line_num})."
                f"{syntax_status}\n\nUpdated Region:\n{preview_snippet}"
            )
        except Exception as e:
            logger.error(f"Replace block error: {e}")
            return f"[ERROR] Failed to replace block in '{file_path}': {e}"

    def view(self, file_path=None, file_paths=None, start_line=None, end_line=None):
        logger.info(f"👀 Viewing file(s): {file_path or file_paths}")
        try:
            if not file_path and not file_paths:
                return "[ERROR] Either 'file_path' or 'file_paths' must be provided."
            if file_paths and not isinstance(file_paths, list):
                return "[ERROR] 'file_paths' must be a list of file paths."
            if file_path and file_paths:
                return "[ERROR] Provide either 'file_path' (single) or 'file_paths' (list), not both."

            image_extensions = {'.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp', '.tiff', '.tif', '.ico'}

            if file_path:
                ext = os.path.splitext(file_path)[1].lower()
                if ext in image_extensions:
                    clean_path = os.path.abspath(file_path.replace("\\", "/"))
                    if not os.path.exists(clean_path):
                        return f"[ERROR] Image file '{clean_path}' does not exist."
                    mime_map = {
                        '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
                        '.png': 'image/png', '.webp': 'image/webp',
                        '.gif': 'image/gif', '.bmp': 'image/bmp',
                        '.tiff': 'image/tiff', '.tif': 'image/tiff',
                        '.ico': 'image/x-icon',
                    }
                    mime_type = mime_map.get(ext, 'image/png')
                    with open(clean_path, "rb") as img_file:
                        b64_data = base64.b64encode(img_file.read()).decode('utf-8')
                    return {
                        "type": "image_payload",
                        "data": [{"mime_type": mime_type, "data": b64_data, "path": clean_path}],
                        "observation": f"[SUCCESS] Loaded image '{clean_path}' inline for visual inspection.",
                    }

            if file_paths:
                image_payloads = []
                text_results = []
                for fp in file_paths:
                    res = self.view(fp, start_line=start_line, end_line=end_line)
                    if isinstance(res, dict) and res.get("type") == "image_payload":
                        image_payloads.extend(res.get("data", []))
                        text_results.append(f"=== {fp} ===\n" + res.get("observation", ""))
                    else:
                        text_results.append(f"=== {fp} ===\n{res}")
                combined = "\n\n".join(text_results)
                truncated = self._truncate_text(combined, self.MAX_BATCH_VIEW_CHARS, "Combined output")
                if image_payloads:
                    return {
                        "type": "image_payload",
                        "data": image_payloads,
                        "observation": f"[SUCCESS] Batch view of {len(file_paths)} files:\n\n{truncated}",
                    }
                return f"[SUCCESS] Batch view of {len(file_paths)} files:\n\n{truncated}"

            try:
                file_size = os.path.getsize(file_path)
                if file_size > self.MAX_FILE_SIZE and (start_line is None and end_line is None):
                    return f"[WARNING] File '{file_path}' is {file_size} bytes (max {self.MAX_FILE_SIZE}). Use line range to view partial content."
            except (OSError, FileNotFoundError):
                pass

            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()
            total_lines = len(lines)

            if start_line and end_line:
                start = max(1, min(start_line, total_lines))
                end = min(end_line, total_lines)
                if start > end:
                    start, end = end, start
                content = "".join(lines[start - 1:end])
                return f"[SUCCESS] Viewed lines {start}-{end} of '{file_path}' ({total_lines} lines total):\n\n{content}"
            if start_line:
                start = max(1, min(start_line, total_lines))
                content = "".join(lines[start - 1:start + 9])
                return f"[SUCCESS] Viewed 10 lines starting from line {start}:\n\n{content}"
            if end_line:
                end = min(end_line, total_lines)
                start = max(1, end - 9)
                content = "".join(lines[start - 1:end])
                return f"[SUCCESS] Viewed 10 lines ending at line {end}:\n\n{content}"

            full_content = "".join(lines)
            if len(full_content) > self.MAX_VIEW_CHARS:
                truncated_content = self._truncate_text(full_content, self.MAX_VIEW_CHARS, f"File '{file_path}'")
                return f"[SUCCESS] Viewed entire file '{file_path}' ({total_lines} lines, {len(full_content)} chars) - TRUNCATED to {self.MAX_VIEW_CHARS} chars:\n\n{truncated_content}"
            return f"[SUCCESS] Viewed entire file '{file_path}' ({total_lines} lines):\n\n{full_content}"
        except Exception as e:
            logger.error(f"View error: {e}")
            return f"[ERROR] Failed to view '{file_path}': {e}"

    def create(self, file_path=None, content="", files=None):
        logger.info(f"📝 Creating file(s): {file_path or files}")
        try:
            if file_path and files:
                return "[ERROR] Provide either 'file_path' (single) or 'files' (list), not both."
            if not file_path and not files:
                return "[ERROR] Either 'file_path' or 'files' must be provided."

            if files:
                if not isinstance(files, list) or not files:
                    return "[ERROR] 'files' must be a non-empty list of objects with 'file_path' and 'content'."
                if len(files) > self.MAX_CREATE_MANY_FILES:
                    return f"[ERROR] Too many files ({len(files)}). Max allowed: {self.MAX_CREATE_MANY_FILES}."
                results = []
                for item in files:
                    fp = item.get("file_path")
                    cont = item.get("content", "")
                    if not fp:
                        results.append("[ERROR] Skipped entry: missing 'file_path'.")
                        continue
                    results.append(self.create(file_path=fp, content=cont))
                return "\n".join(results)

            dir_name = os.path.dirname(os.path.abspath(file_path))
            if dir_name:
                os.makedirs(dir_name, exist_ok=True)
            if len(content) > 10 * 1024 * 1024:
                return f"[ERROR] File content too large ({len(content)} bytes). Max allowed 10MB."
            with open(file_path, "w", encoding="utf-8", newline="\n") as f:
                f.write(content)
            syntax_status = self._validate_syntax(file_path)
            if "CRITICAL" in syntax_status:
                return f"[ERROR] Created file '{file_path}', but syntax is invalid!{syntax_status}"
            return f"[SUCCESS] Created file '{file_path}' successfully ({len(content)} chars).{syntax_status}"
        except Exception as e:
            logger.error(f"Create error: {e}")
            return f"[ERROR] Failed to create file '{file_path}': {e}"