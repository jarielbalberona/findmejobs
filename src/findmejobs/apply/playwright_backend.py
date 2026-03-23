from __future__ import annotations

from pathlib import Path

from findmejobs.apply.browser import BrowserBackend, BrowserField, BrowserStepSnapshot


class PlaywrightBrowserBackend(BrowserBackend):
    def __init__(self) -> None:
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None

    def open(self, *, url: str, browser_profile: str | None = None, browser_profile_dir: Path | None = None) -> BrowserStepSnapshot:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError("playwright_not_installed: install with pip install -e '.[browser]' and playwright install chromium") from exc
        self._playwright = sync_playwright().start()
        chromium = self._playwright.chromium
        if browser_profile_dir is not None:
            self._context = chromium.launch_persistent_context(str(browser_profile_dir), headless=False)
            self._page = self._context.pages[0] if self._context.pages else self._context.new_page()
        else:
            self._browser = chromium.launch(headless=False)
            self._context = self._browser.new_context()
            self._page = self._context.new_page()
        self._page.goto(url, wait_until="domcontentloaded")
        return self._snapshot("open", browser_profile)

    def fill(self, field: BrowserField, value: str) -> None:
        locator = self._locator_for_field(field)
        locator.fill(value)

    def upload(self, field: BrowserField, file_path: Path) -> None:
        locator = self._locator_for_field(field)
        locator.set_input_files(str(file_path))

    def click_next(self, label: str | None = None) -> BrowserStepSnapshot:
        button = self._next_button(label)
        button.click()
        self._page.wait_for_load_state("domcontentloaded")
        return self._snapshot("next", label)

    def close(self) -> None:
        if self._context is not None:
            self._context.close()
        if self._browser is not None:
            self._browser.close()
        if self._playwright is not None:
            self._playwright.stop()

    def _snapshot(self, step_prefix: str, label: str | None) -> BrowserStepSnapshot:
        fields = []
        for index, handle in enumerate(self._page.locator("input, textarea, select").all()):
            fields.append(self._extract_field(handle, index))
        next_label = None
        submit_visible = False
        for button in self._page.locator("button, input[type=submit]").all():
            text = (button.inner_text() or button.get_attribute("value") or "").strip()
            if not text:
                continue
            lowered = text.casefold()
            if "submit" in lowered or "apply" in lowered:
                submit_visible = True
            elif any(token in lowered for token in ("next", "continue", "review", "preview")) and next_label is None:
                next_label = text
        parse_confidence = 0.92 if fields else 0.55
        return BrowserStepSnapshot(
            step_id=f"{step_prefix}-{len(fields)}",
            step_label=label or (self._page.title() or "Application form"),
            page_url=self._page.url,
            fields=fields,
            parse_confidence=parse_confidence,
            next_action_label=next_label,
            submit_visible=submit_visible,
        )

    def _extract_field(self, handle, index: int) -> BrowserField:
        tag_name = handle.evaluate("(el) => el.tagName.toLowerCase()")
        raw_type = (handle.get_attribute("type") or tag_name or "unknown").casefold()
        field_type = raw_type if raw_type in {"text", "email", "tel", "url", "textarea", "select", "checkbox", "radio", "file"} else "unknown"
        field_id = handle.get_attribute("name") or handle.get_attribute("id") or f"field-{index}"
        label = (
            handle.get_attribute("aria-label")
            or handle.get_attribute("placeholder")
            or self._label_for(handle.get_attribute("id"))
            or field_id
        )
        value = handle.input_value() if tag_name in {"input", "textarea", "select"} else None
        required = bool(handle.get_attribute("required"))
        options = []
        if tag_name == "select":
            options = [opt.inner_text().strip() for opt in handle.locator("option").all()]
        return BrowserField(field_id=field_id, label=label, field_type=field_type, value=value, required=required, options=options)

    def _label_for(self, element_id: str | None) -> str | None:
        if not element_id:
            return None
        label = self._page.locator(f"label[for='{element_id}']").first
        if label.count() == 0:
            return None
        return label.inner_text().strip()

    def _locator_for_field(self, field: BrowserField):
        locator = self._page.locator(f"[name='{field.field_id}'], #{field.field_id}").first
        if locator.count() == 0:
            raise RuntimeError(f"browser_field_not_found:{field.field_id}")
        return locator

    def _next_button(self, label: str | None):
        if label:
            button = self._page.get_by_role("button", name=label)
            if button.count() > 0:
                return button.first
        for button in self._page.locator("button, input[type=button], input[type=submit]").all():
            text = (button.inner_text() or button.get_attribute("value") or "").strip()
            if text and any(token in text.casefold() for token in ("next", "continue", "review", "preview")):
                return button
        raise RuntimeError("browser_next_action_not_found")
