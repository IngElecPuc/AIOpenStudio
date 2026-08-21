"""Tkinter tab for queued, supervised Fooocus image workflows."""

from __future__ import annotations

import asyncio
import secrets
import tkinter as tk
from collections.abc import Callable, Coroutine, Sequence
from concurrent.futures import Future
from dataclasses import dataclass
from functools import partial
from importlib import import_module
from pathlib import Path
from queue import Empty, SimpleQueue
from tkinter import filedialog, messagebox, scrolledtext, ttk
from typing import Any, TypeVar

from aiopenstudio.core.contracts import (
    DescribeContent,
    EnhancementStep,
    EnhanceOptions,
    EnhanceOrder,
    EnhancePromptSource,
    ImageGenerationCapabilities,
    ImageGenerationEvent,
    ImageGenerationEventKind,
    ImageGenerationOptions,
    ImageGenerationRequest,
    ImageOperation,
    ImagePerformance,
    ImagePromptKind,
    ImagePromptReference,
    InpaintMode,
    ModelDescriptor,
    OutpaintDirection,
    RuntimeHealth,
)
from aiopenstudio.services import ImageGenerationService
from aiopenstudio.ui.async_runner import AsyncLoopRunner

T = TypeVar("T")
_IMAGE_TYPES = (("Imágenes compatibles", "*.png *.jpg *.jpeg *.bmp"),)
_OPERATIONS = {
    ImageOperation.TEXT_TO_IMAGE: "Texto a imagen",
    ImageOperation.VARY_SUBTLE: "Variación sutil",
    ImageOperation.VARY_STRONG: "Variación fuerte",
    ImageOperation.UPSCALE_1_5: "Upscale 1,5×",
    ImageOperation.UPSCALE_2: "Upscale 2×",
    ImageOperation.UPSCALE_FAST_2: "Upscale rápido 2×",
    ImageOperation.INPAINT: "Inpaint",
    ImageOperation.OUTPAINT: "Outpaint",
    ImageOperation.IMAGE_PROMPT: "Image Prompt",
    ImageOperation.DESCRIBE: "Describe",
    ImageOperation.ENHANCE: "Enhance",
}
_OPERATION_VALUES = {label: value for value, label in _OPERATIONS.items()}
_KINDS = {
    ImagePromptKind.IMAGE_PROMPT: "Image Prompt",
    ImagePromptKind.PYRA_CANNY: "PyraCanny",
    ImagePromptKind.CPDS: "CPDS",
    ImagePromptKind.FACE_SWAP: "FaceSwap",
}
_KIND_VALUES = {label: value for value, label in _KINDS.items()}


@dataclass(slots=True)
class _ReferenceDraft:
    path: Path
    kind: ImagePromptKind = ImagePromptKind.IMAGE_PROMPT
    stop_at: float = 0.5
    weight: float = 0.6
    enabled: bool = True


class EnhancementDialog(tk.Toplevel):
    """Edit the complete set of controls for one enhancement region."""

    def __init__(
        self,
        parent: tk.Misc,
        step: EnhancementStep,
        save: Callable[[EnhancementStep], None],
    ) -> None:
        super().__init__(parent)
        self.title("Etapa Enhance")
        self.transient(parent.winfo_toplevel())
        self.grab_set()
        self._save_callback = save
        self._enabled = tk.BooleanVar(value=step.enabled)
        self._detection = tk.StringVar(value=step.detection_prompt)
        self._positive = tk.StringVar(value=step.positive_prompt)
        self._negative = tk.StringVar(value=step.negative_prompt)
        self._mask_model = tk.StringVar(value=step.mask_model)
        self._cloth = tk.StringVar(value=step.cloth_category)
        self._sam = tk.StringVar(value=step.sam_model)
        self._text = tk.DoubleVar(value=step.text_threshold)
        self._box = tk.DoubleVar(value=step.box_threshold)
        self._maximum = tk.IntVar(value=step.max_detections)
        self._mode = tk.StringVar(value=step.inpaint_mode.value)
        self._denoise = tk.DoubleVar(value=step.denoising_strength)
        self._field = tk.DoubleVar(value=step.respective_field)
        self._erode = tk.IntVar(value=step.mask_erode_or_dilate)
        self._invert = tk.BooleanVar(value=step.invert_mask)
        self._build()

    def _build(self) -> None:
        body = ttk.Frame(self, padding=12)
        body.pack(fill=tk.BOTH, expand=True)
        body.columnconfigure(1, weight=1)
        ttk.Checkbutton(body, text="Habilitada", variable=self._enabled).grid(
            row=0, column=0, columnspan=2, sticky="w"
        )
        for row, (label, text_variable) in enumerate(
            (
                ("Detección", self._detection),
                ("Prompt positivo", self._positive),
                ("Prompt negativo", self._negative),
            ),
            start=1,
        ):
            ttk.Label(body, text=label).grid(row=row, column=0, sticky="w", pady=(6, 0))
            ttk.Entry(body, textvariable=text_variable).grid(
                row=row, column=1, sticky="ew", padx=(8, 0), pady=(6, 0)
            )
        selectors = ttk.Frame(body)
        selectors.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        selector_specs = (
            (
                "Máscara",
                self._mask_model,
                (
                    "sam",
                    "u2net",
                    "u2netp",
                    "u2net_human_seg",
                    "u2net_cloth_seg",
                    "silueta",
                    "isnet-general-use",
                    "isnet-anime",
                ),
            ),
            ("SAM", self._sam, ("vit_b", "vit_l", "vit_h")),
            ("Ropa", self._cloth, ("full", "upper", "lower")),
            ("Inpaint", self._mode, tuple(value.value for value in InpaintMode)),
        )
        for label, selector_variable, values in selector_specs:
            ttk.Label(selectors, text=label).pack(side=tk.LEFT)
            ttk.Combobox(
                selectors,
                textvariable=selector_variable,
                values=values,
                state="readonly",
                width=12,
            ).pack(side=tk.LEFT, padx=(4, 8))
        numeric = ttk.Frame(body)
        numeric.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        numeric_specs = (
            ("Text", self._text, 0, 1, 0.05),
            ("Box", self._box, 0, 1, 0.05),
            ("Máx.", self._maximum, 0, 100, 1),
            ("Denoise", self._denoise, 0, 1, 0.05),
            ("Campo", self._field, 0, 1, 0.05),
            ("Erosión", self._erode, -64, 64, 1),
        )
        for label, numeric_variable, lower, upper, increment in numeric_specs:
            ttk.Label(numeric, text=label).pack(side=tk.LEFT)
            ttk.Spinbox(
                numeric,
                from_=lower,
                to=upper,
                increment=increment,
                textvariable=numeric_variable,
                width=6,
            ).pack(side=tk.LEFT, padx=(3, 7))
        ttk.Checkbutton(body, text="Invertir máscara", variable=self._invert).grid(
            row=6, column=0, columnspan=2, sticky="w", pady=(8, 0)
        )
        actions = ttk.Frame(body)
        actions.grid(row=7, column=0, columnspan=2, sticky="e", pady=(12, 0))
        ttk.Button(actions, text="Cancelar", command=self.destroy).pack(side=tk.RIGHT)
        ttk.Button(actions, text="Guardar", command=self._save).pack(side=tk.RIGHT, padx=(0, 6))

    def _save(self) -> None:
        try:
            result = EnhancementStep(
                enabled=self._enabled.get(),
                detection_prompt=self._detection.get().strip(),
                positive_prompt=self._positive.get().strip(),
                negative_prompt=self._negative.get().strip(),
                mask_model=self._mask_model.get(),
                cloth_category=self._cloth.get(),
                sam_model=self._sam.get(),
                text_threshold=self._text.get(),
                box_threshold=self._box.get(),
                max_detections=self._maximum.get(),
                inpaint_mode=InpaintMode(self._mode.get()),
                denoising_strength=self._denoise.get(),
                respective_field=self._field.get(),
                mask_erode_or_dilate=self._erode.get(),
                invert_mask=self._invert.get(),
            )
        except (tk.TclError, ValueError) as error:
            messagebox.showerror("Enhance", str(error), parent=self)
            return
        self._save_callback(result)
        self.destroy()


class FooocusTab(ttk.Frame):
    def __init__(
        self,
        parent: tk.Misc,
        service: ImageGenerationService,
        runner: AsyncLoopRunner,
    ) -> None:
        super().__init__(parent, padding=12)
        self._service = service
        self._runner = runner
        self._callbacks: SimpleQueue[Callable[[], None]] = SimpleQueue()
        self._models: dict[str, ModelDescriptor] = {}
        self._capabilities = ImageGenerationCapabilities()
        self._references: list[_ReferenceDraft] = []
        self._enhancements: list[EnhancementStep] = [EnhancementStep()]
        self._gallery_paths: list[Path] = []
        self._thumbnails: list[Any] = []
        self._gallery_index = -1
        self._gallery_revision = 0
        self._model_name = tk.StringVar()
        self._operation = tk.StringVar(value=_OPERATIONS[ImageOperation.TEXT_TO_IMAGE])
        self._performance = tk.StringVar(value=ImagePerformance.SPEED.value)
        self._dimensions = tk.StringVar(value="1024×1024")
        self._count = tk.IntVar(value=1)
        self._seed = tk.StringVar()
        self._guidance = tk.DoubleVar(value=4.0)
        self._sharpness = tk.DoubleVar(value=2.0)
        self._styles = tk.StringVar(value="Fooocus V2")
        self._format = tk.StringVar(value="png")
        self._source = tk.StringVar()
        self._mask = tk.StringVar()
        self._inpaint_mode = tk.StringVar(value=InpaintMode.DEFAULT.value)
        self._inpaint_prompt = tk.StringVar()
        self._mix_references = tk.BooleanVar(value=False)
        self._describe_photo = tk.BooleanVar(value=True)
        self._describe_anime = tk.BooleanVar(value=False)
        self._describe_styles = tk.BooleanVar(value=True)
        self._outpaint = {value: tk.BooleanVar(value=False) for value in OutpaintDirection}
        self._reference_kind = tk.StringVar(value=_KINDS[ImagePromptKind.IMAGE_PROMPT])
        self._reference_stop = tk.DoubleVar(value=0.5)
        self._reference_weight = tk.DoubleVar(value=0.6)
        self._enhance_uov = tk.StringVar(value="Sin variación/upscale")
        self._enhance_order = tk.StringVar(value=EnhanceOrder.BEFORE.value)
        self._enhance_prompt = tk.StringVar(value=EnhancePromptSource.ORIGINAL.value)
        self._enhance_save_final = tk.BooleanVar(value=False)
        self._remember_gallery = tk.BooleanVar(value=False)
        self._status = tk.StringVar(value="Fooocus: comprobación pendiente")
        self._progress_text = tk.StringVar(value="Sin operación activa")
        self._build()
        self.after(50, self._drain_callbacks)
        self.refresh()

    def _build(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        header = ttk.Frame(self)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        header.columnconfigure(0, weight=1)
        ttk.Label(header, textvariable=self._status).grid(row=0, column=0, sticky="w")
        ttk.Button(header, text="Actualizar", command=self.refresh).grid(row=0, column=1)
        panes = ttk.Panedwindow(self, orient=tk.HORIZONTAL)
        panes.grid(row=1, column=0, sticky="nsew")
        controls = ttk.Frame(panes, padding=(0, 0, 10, 0))
        results = ttk.Frame(panes)
        panes.add(controls, weight=2)
        panes.add(results, weight=3)
        self._build_controls(controls)
        self._build_results(results)
        progress = ttk.Frame(self)
        progress.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        progress.columnconfigure(0, weight=1)
        self._progress = ttk.Progressbar(progress, maximum=100, mode="determinate")
        self._progress.grid(row=0, column=0, sticky="ew")
        ttk.Label(progress, textvariable=self._progress_text).grid(row=0, column=1, padx=(8, 0))

    def _build_controls(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(1, weight=1)
        parent.rowconfigure(6, weight=1)
        ttk.Label(parent, text="Checkpoint").grid(row=0, column=0, sticky="w")
        self._model_selector = ttk.Combobox(parent, textvariable=self._model_name, state="readonly")
        self._model_selector.grid(row=0, column=1, sticky="ew", padx=(8, 0))
        ttk.Label(parent, text="Operación").grid(row=1, column=0, sticky="w", pady=(6, 0))
        self._operation_selector = ttk.Combobox(
            parent, textvariable=self._operation, state="readonly"
        )
        self._operation_selector.grid(row=1, column=1, sticky="ew", padx=(8, 0), pady=(6, 0))
        self._operation_selector.bind("<<ComboboxSelected>>", self._operation_changed)
        compact = ttk.Frame(parent)
        compact.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        compact_specs = (
            (
                "Rendimiento",
                self._performance,
                tuple(value.value for value in ImagePerformance),
                13,
            ),
            (
                "Tamaño",
                self._dimensions,
                ("1024×1024", "1152×896", "896×1152", "1344×768", "768×1344"),
                11,
            ),
            ("Formato", self._format, ("png", "jpeg", "webp"), 7),
        )
        for label, compact_variable, values, width in compact_specs:
            ttk.Label(compact, text=label).pack(side=tk.LEFT)
            ttk.Combobox(
                compact,
                textvariable=compact_variable,
                values=values,
                state="readonly",
                width=width,
            ).pack(side=tk.LEFT, padx=(4, 8))
        numeric = ttk.Frame(parent)
        numeric.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        for label, numeric_variable, start, end, increment, width in (
            ("Imágenes", self._count, 1, 8, 1, 4),
            ("Guidance", self._guidance, 1, 30, 0.5, 6),
            ("Sharpness", self._sharpness, 0, 30, 0.5, 6),
        ):
            ttk.Label(numeric, text=label).pack(side=tk.LEFT)
            ttk.Spinbox(
                numeric,
                from_=start,
                to=end,
                increment=increment,
                textvariable=numeric_variable,
                width=width,
            ).pack(side=tk.LEFT, padx=(4, 8))
        ttk.Label(numeric, text="Seed").pack(side=tk.LEFT)
        ttk.Entry(numeric, textvariable=self._seed, width=12).pack(side=tk.LEFT, padx=(4, 0))
        ttk.Button(
            numeric,
            text="⚅",
            width=3,
            command=self._randomize_seed,
        ).pack(side=tk.LEFT, padx=(4, 0))
        ttk.Label(parent, text="Estilos (separados por coma)").grid(
            row=4, column=0, columnspan=2, sticky="w", pady=(6, 0)
        )
        ttk.Entry(parent, textvariable=self._styles).grid(
            row=5, column=0, columnspan=2, sticky="ew"
        )
        notebook = ttk.Notebook(parent)
        notebook.grid(row=6, column=0, columnspan=2, sticky="nsew", pady=(8, 0))
        for title, builder in (
            ("Prompts", self._build_prompts),
            ("Fuente y máscara", self._build_sources),
            ("Referencias", self._build_references),
            ("Enhance", self._build_enhance),
        ):
            frame = ttk.Frame(notebook, padding=6)
            notebook.add(frame, text=title)
            builder(frame)
        actions = ttk.Frame(parent)
        actions.grid(row=7, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        self._generate_button = ttk.Button(actions, text="Añadir a cola", command=self._start)
        self._generate_button.pack(side=tk.LEFT)
        ttk.Button(actions, text="Cancelar seleccionado", command=self._cancel).pack(
            side=tk.LEFT, padx=(6, 0)
        )

    def _build_prompts(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(1, weight=1)
        ttk.Label(parent, text="Prompt").grid(row=0, column=0, sticky="w")
        self._prompt = scrolledtext.ScrolledText(parent, height=7, wrap=tk.WORD)
        self._prompt.grid(row=1, column=0, sticky="nsew")
        ttk.Label(parent, text="Prompt negativo").grid(row=2, column=0, sticky="w", pady=(6, 0))
        self._negative = scrolledtext.ScrolledText(parent, height=3, wrap=tk.WORD)
        self._negative.grid(row=3, column=0, sticky="ew")

    def _randomize_seed(self) -> None:
        self._seed.set(str(secrets.randbelow(2**63)))

    def _build_sources(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(1, weight=1)
        for row, (label, source_variable) in enumerate(
            (("Imagen fuente", self._source), ("Máscara", self._mask))
        ):
            ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=(4, 0))
            ttk.Entry(parent, textvariable=source_variable, state="readonly").grid(
                row=row, column=1, sticky="ew", padx=(6, 0), pady=(4, 0)
            )
            ttk.Button(
                parent,
                text="Cargar…",
                command=partial(self._choose_image, source_variable),
            ).grid(
                row=row, column=2, padx=(6, 0), pady=(4, 0)
            )
            ttk.Button(
                parent,
                text="Ver",
                command=partial(self._preview_variable, source_variable),
            ).grid(
                row=row, column=3, padx=(4, 0), pady=(4, 0)
            )
            ttk.Button(
                parent,
                text="Quitar",
                command=partial(source_variable.set, ""),
            ).grid(
                row=row, column=4, padx=(4, 0), pady=(4, 0)
            )
        modes = ttk.Frame(parent)
        modes.grid(row=2, column=0, columnspan=5, sticky="ew", pady=(8, 0))
        ttk.Label(modes, text="Inpaint").pack(side=tk.LEFT)
        ttk.Combobox(
            modes,
            textvariable=self._inpaint_mode,
            values=tuple(value.value for value in InpaintMode),
            state="readonly",
            width=10,
        ).pack(side=tk.LEFT, padx=(4, 8))
        ttk.Label(modes, text="Prompt adicional").pack(side=tk.LEFT)
        ttk.Entry(modes, textvariable=self._inpaint_prompt).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=(4, 0)
        )
        directions = ttk.Frame(parent)
        directions.grid(row=3, column=0, columnspan=5, sticky="w", pady=(8, 0))
        ttk.Label(directions, text="Outpaint:").pack(side=tk.LEFT)
        for direction, direction_variable in self._outpaint.items():
            ttk.Checkbutton(
                directions,
                text=direction.value,
                variable=direction_variable,
            ).pack(
                side=tk.LEFT, padx=(5, 0)
            )
        describe = ttk.Frame(parent)
        describe.grid(row=4, column=0, columnspan=5, sticky="w", pady=(8, 0))
        ttk.Label(describe, text="Describe:").pack(side=tk.LEFT)
        ttk.Checkbutton(describe, text="Fotografía", variable=self._describe_photo).pack(
            side=tk.LEFT, padx=(5, 0)
        )
        ttk.Checkbutton(describe, text="Arte/Anime", variable=self._describe_anime).pack(
            side=tk.LEFT, padx=(5, 0)
        )
        ttk.Checkbutton(describe, text="Aplicar estilos", variable=self._describe_styles).pack(
            side=tk.LEFT, padx=(5, 0)
        )

    def _build_references(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)
        columns = ("enabled", "kind", "stop", "weight", "file")
        self._reference_tree = ttk.Treeview(parent, columns=columns, show="headings", height=6)
        for column, label, width in (
            ("enabled", "Usar", 45),
            ("kind", "Tipo", 95),
            ("stop", "Stop", 50),
            ("weight", "Peso", 50),
            ("file", "Archivo", 180),
        ):
            self._reference_tree.heading(column, text=label)
            self._reference_tree.column(column, width=width, stretch=column == "file")
        self._reference_tree.grid(row=0, column=0, sticky="nsew")
        self._reference_tree.bind("<<TreeviewSelect>>", self._reference_selected)
        self._reference_tree.bind("<Double-1>", lambda _: self._toggle_reference())
        actions = ttk.Frame(parent)
        actions.grid(row=1, column=0, sticky="ew", pady=(6, 0))
        for text, command in (
            ("Agregar…", self._add_references),
            ("Quitar", self._remove_reference),
            ("↑", partial(self._move_reference, -1)),
            ("↓", partial(self._move_reference, 1)),
            ("Usar/no usar", self._toggle_reference),
            ("Ver", self._preview_reference),
        ):
            ttk.Button(actions, text=text, command=command).pack(side=tk.LEFT, padx=(0, 4))
        editor = ttk.Frame(parent)
        editor.grid(row=2, column=0, sticky="ew", pady=(6, 0))
        ttk.Label(editor, text="Tipo").pack(side=tk.LEFT)
        self._reference_kind_selector = ttk.Combobox(
            editor,
            textvariable=self._reference_kind,
            values=tuple(_KIND_VALUES),
            state="readonly",
            width=13,
        )
        self._reference_kind_selector.pack(side=tk.LEFT, padx=(4, 8))
        ttk.Label(editor, text="Stop").pack(side=tk.LEFT)
        ttk.Spinbox(
            editor,
            from_=0,
            to=1,
            increment=0.05,
            textvariable=self._reference_stop,
            width=6,
        ).pack(side=tk.LEFT, padx=(4, 8))
        ttk.Label(editor, text="Peso").pack(side=tk.LEFT)
        ttk.Spinbox(
            editor,
            from_=0,
            to=2,
            increment=0.05,
            textvariable=self._reference_weight,
            width=6,
        ).pack(side=tk.LEFT, padx=(4, 8))
        ttk.Button(editor, text="Aplicar", command=self._apply_reference).pack(side=tk.LEFT)
        ttk.Checkbutton(
            parent,
            text="Mezclar referencias con variación/upscale o inpaint",
            variable=self._mix_references,
        ).grid(row=3, column=0, sticky="w", pady=(6, 0))

    def _build_enhance(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        settings = ttk.Frame(parent)
        settings.grid(row=0, column=0, sticky="ew")
        uov_values = ("Sin variación/upscale",) + tuple(
            _OPERATIONS[value]
            for value in (
                ImageOperation.VARY_SUBTLE,
                ImageOperation.VARY_STRONG,
                ImageOperation.UPSCALE_1_5,
                ImageOperation.UPSCALE_2,
                ImageOperation.UPSCALE_FAST_2,
            )
        )
        ttk.Label(settings, text="Variación/upscale").pack(side=tk.LEFT)
        ttk.Combobox(
            settings,
            textvariable=self._enhance_uov,
            values=uov_values,
            state="readonly",
            width=20,
        ).pack(side=tk.LEFT, padx=(4, 8))
        ttk.Label(settings, text="Orden").pack(side=tk.LEFT)
        ttk.Combobox(
            settings,
            textvariable=self._enhance_order,
            values=tuple(value.value for value in EnhanceOrder),
            state="readonly",
            width=8,
        ).pack(side=tk.LEFT, padx=(4, 8))
        ttk.Label(settings, text="Prompt").pack(side=tk.LEFT)
        ttk.Combobox(
            settings,
            textvariable=self._enhance_prompt,
            values=tuple(value.value for value in EnhancePromptSource),
            state="readonly",
            width=10,
        ).pack(side=tk.LEFT, padx=(4, 0))
        columns = ("enabled", "detection", "mask")
        self._enhance_tree = ttk.Treeview(parent, columns=columns, show="headings", height=4)
        for column, label, width in (
            ("enabled", "Usar", 45),
            ("detection", "Detección", 210),
            ("mask", "Máscara", 100),
        ):
            self._enhance_tree.heading(column, text=label)
            self._enhance_tree.column(column, width=width, stretch=column == "detection")
        self._enhance_tree.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        actions = ttk.Frame(parent)
        actions.grid(row=2, column=0, sticky="ew", pady=(6, 0))
        ttk.Button(actions, text="Agregar etapa", command=self._add_enhancement).pack(side=tk.LEFT)
        ttk.Button(actions, text="Editar…", command=self._edit_enhancement).pack(
            side=tk.LEFT, padx=(4, 0)
        )
        ttk.Button(actions, text="Quitar", command=self._remove_enhancement).pack(
            side=tk.LEFT, padx=(4, 0)
        )
        ttk.Checkbutton(
            actions,
            text="Guardar sólo imagen final",
            variable=self._enhance_save_final,
        ).pack(side=tk.LEFT, padx=(12, 0))
        self._refresh_enhancements()

    def _build_results(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(4, weight=1)
        ttk.Label(parent, text="Cola y ejecuciones").grid(row=0, column=0, sticky="w")
        self._queue = ttk.Treeview(
            parent, columns=("id", "model", "status"), show="headings", height=5
        )
        for column, label, width in (
            ("id", "Ejecución", 90),
            ("model", "Checkpoint", 150),
            ("status", "Estado", 170),
        ):
            self._queue.heading(column, text=label)
            self._queue.column(column, width=width, stretch=column != "id")
        self._queue.grid(row=1, column=0, sticky="ew", pady=(4, 8))
        header = ttk.Frame(parent)
        header.grid(row=2, column=0, sticky="ew")
        ttk.Label(header, text="Galería").pack(side=tk.LEFT)
        ttk.Checkbutton(
            header,
            text="Recordar índice entre reinicios",
            variable=self._remember_gallery,
            command=self._gallery_memory_changed,
        ).pack(side=tk.LEFT, padx=(10, 0))
        ttk.Button(header, text="Olvidar galería", command=self._forget_gallery).pack(side=tk.RIGHT)
        navigation = ttk.Frame(parent)
        navigation.grid(row=3, column=0, sticky="ew", pady=(4, 0))
        ttk.Button(navigation, text="◀", command=partial(self._navigate_gallery, -1)).pack(
            side=tk.LEFT
        )
        ttk.Button(navigation, text="Ver", command=self._preview_gallery).pack(side=tk.LEFT, padx=4)
        ttk.Button(navigation, text="▶", command=partial(self._navigate_gallery, 1)).pack(
            side=tk.LEFT
        )
        self._gallery = ttk.Frame(parent)
        self._gallery.grid(row=4, column=0, sticky="nsew", pady=(4, 0))
        for gallery_column in range(3):
            self._gallery.columnconfigure(gallery_column, weight=1)
        ttk.Label(parent, text="Descripción").grid(row=5, column=0, sticky="w", pady=(8, 0))
        self._description = scrolledtext.ScrolledText(
            parent, height=4, wrap=tk.WORD, state=tk.DISABLED
        )
        self._description.grid(row=6, column=0, sticky="ew")

    def refresh(self) -> None:
        self._status.set("Fooocus: comprobando instalación, esquema y checkpoints…")
        self._submit(self._refresh(), self._refreshed)

    async def _refresh(
        self,
    ) -> tuple[
        RuntimeHealth,
        Sequence[ModelDescriptor],
        Sequence[str],
        ImageGenerationCapabilities,
        bool,
        tuple[Path, ...],
    ]:
        health = await self._service.health()
        models = await self._service.refresh_models()
        styles: Sequence[str] = ()
        if health is not RuntimeHealth.UNAVAILABLE:
            styles = await self._service.list_styles()
        capabilities = await self._service.image_capabilities()
        remember = await self._service.gallery_memory_enabled()
        gallery = await self._service.list_gallery() if remember else ()
        return health, models, styles, capabilities, remember, gallery

    def _refreshed(
        self,
        payload: tuple[
            RuntimeHealth,
            Sequence[ModelDescriptor],
            Sequence[str],
            ImageGenerationCapabilities,
            bool,
            tuple[Path, ...],
        ],
    ) -> None:
        health, models, styles, capabilities, remember, gallery = payload
        self._capabilities = capabilities
        self._models = {model.display_name: model for model in models}
        names = tuple(self._models)
        self._model_selector.configure(values=names)
        if names and self._model_name.get() not in self._models:
            self._model_name.set(names[0])
        if styles:
            self._styles.set(
                next(
                    (value for value in styles if value.casefold() == "fooocus v2"),
                    styles[0],
                )
            )
        operation_labels = tuple(
            _OPERATIONS[value] for value in ImageOperation if value in capabilities.operations
        )
        self._operation_selector.configure(values=operation_labels)
        if operation_labels and self._operation.get() not in operation_labels:
            self._operation.set(operation_labels[0])
        kind_labels = tuple(
            _KINDS[value] for value in ImagePromptKind if value in capabilities.prompt_kinds
        )
        self._reference_kind_selector.configure(values=kind_labels)
        self._remember_gallery.set(remember)
        if gallery:
            self._replace_gallery(gallery)
        issues = self._service.preflight()
        if issues:
            self._status.set(f"Fooocus no disponible: {issues[0]}")
        else:
            self._status.set(
                f"Fooocus {health.value}: {len(names)} checkpoint(s); "
                f"esquema {capabilities.schema_source}"
            )
        state = tk.NORMAL if names and not issues else tk.DISABLED
        self._generate_button.configure(state=state)

    def _start(self) -> None:
        descriptor = self._models.get(self._model_name.get())
        operation = _OPERATION_VALUES.get(self._operation.get())
        if descriptor is None or operation is None:
            messagebox.showinfo("Fooocus", "Selecciona un checkpoint y una operación.")
            return
        try:
            width_text, height_text = self._dimensions.get().split("×", maxsplit=1)
            seed_text = self._seed.get().strip()
            request = ImageGenerationRequest(
                operation_id=self._service.create_operation_id(),
                model=descriptor.id,
                prompt=self._prompt.get("1.0", tk.END).strip(),
                negative_prompt=self._negative.get("1.0", tk.END).strip(),
                options=ImageGenerationOptions(
                    width=int(width_text),
                    height=int(height_text),
                    image_count=self._count.get(),
                    seed=int(seed_text) if seed_text else None,
                    performance=ImagePerformance(self._performance.get()),
                    guidance_scale=self._guidance.get(),
                    sharpness=self._sharpness.get(),
                    styles=tuple(
                        value.strip() for value in self._styles.get().split(",") if value.strip()
                    ),
                    output_format=self._format.get(),
                ),
                operation=operation,
                source_image=Path(self._source.get()) if self._source.get() else None,
                mask_image=Path(self._mask.get()) if self._mask.get() else None,
                outpaint_directions=tuple(
                    value for value, enabled in self._outpaint.items() if enabled.get()
                ),
                inpaint_mode=InpaintMode(self._inpaint_mode.get()),
                inpaint_prompt=self._inpaint_prompt.get().strip(),
                references=tuple(
                    ImagePromptReference(
                        path=value.path,
                        kind=value.kind,
                        stop_at=value.stop_at,
                        weight=value.weight,
                        enabled=value.enabled,
                    )
                    for value in self._references
                ),
                mix_references=self._mix_references.get(),
                describe_content=tuple(
                    content
                    for content, enabled in (
                        (DescribeContent.PHOTOGRAPH, self._describe_photo),
                        (DescribeContent.ART_ANIME, self._describe_anime),
                    )
                    if enabled.get()
                ),
                describe_apply_styles=self._describe_styles.get(),
                enhance=self._enhance_options() if operation is ImageOperation.ENHANCE else None,
            )
        except (tk.TclError, TypeError, ValueError) as error:
            messagebox.showerror("Parámetros Fooocus", str(error))
            return
        issues = self._service.preflight_for(request)
        if issues:
            messagebox.showwarning("Activos Fooocus faltantes", "\n\n".join(issues))
            return
        self._queue.insert(
            "",
            tk.END,
            iid=request.operation_id,
            values=(request.operation_id[:8], descriptor.display_name, "queued"),
        )
        self._status.set("Trabajo añadido a la cola local.")
        self._submit(self._consume(request), self._ignore_result)

    def _enhance_options(self) -> EnhanceOptions:
        return EnhanceOptions(
            uov_operation=_OPERATION_VALUES.get(self._enhance_uov.get()),
            order=EnhanceOrder(self._enhance_order.get()),
            prompt_source=EnhancePromptSource(self._enhance_prompt.get()),
            steps=tuple(self._enhancements),
            save_only_final=self._enhance_save_final.get(),
        )

    async def _consume(self, request: ImageGenerationRequest) -> None:
        async for event in self._service.stream_generation(request):
            self._post(partial(self._handle_event, event))

    def _handle_event(self, event: ImageGenerationEvent) -> None:
        if self._queue.exists(event.operation_id):
            status = event.kind.value
            if event.progress is not None:
                status = event.progress.stage.value
                if event.progress.fraction is not None:
                    self._progress.stop()
                    self._progress.configure(
                        mode="determinate", value=event.progress.fraction * 100
                    )
                elif event.kind is not ImageGenerationEventKind.QUEUED:
                    self._progress.configure(mode="indeterminate")
                    self._progress.start(12)
                self._progress_text.set(event.progress.detail or status)
            values = self._queue.item(event.operation_id, "values")
            self._queue.item(event.operation_id, values=(values[0], values[1], status))
        if event.kind is ImageGenerationEventKind.IMAGE and event.source_path is not None:
            self._submit(self._thumbnail(event.source_path), self._add_thumbnail)
        elif event.kind is ImageGenerationEventKind.DESCRIPTION and event.description:
            self._description.configure(state=tk.NORMAL)
            self._description.delete("1.0", tk.END)
            self._description.insert("1.0", event.description)
            self._description.configure(state=tk.DISABLED)
            self._status.set("Descripción completada.")
        elif event.kind is ImageGenerationEventKind.COMPLETED and event.result is not None:
            self._progress.stop()
            self._progress.configure(mode="determinate", value=100)
            self._status.set(f"Completado: {event.result.run_directory}")
        elif event.kind is ImageGenerationEventKind.CANCELLED:
            self._progress.stop()
            self._progress.configure(mode="determinate", value=0)
            self._status.set("Generación cancelada; recursos restaurados.")
        elif event.kind is ImageGenerationEventKind.ERROR:
            self._progress.stop()
            self._progress.configure(mode="determinate", value=0)
            self._status.set("La operación falló; consulta el mensaje y los metadatos.")
            messagebox.showerror("Fooocus", event.message or "Error desconocido")

    def _choose_image(self, variable: tk.StringVar) -> None:
        path = filedialog.askopenfilename(parent=self, filetypes=_IMAGE_TYPES)
        if path:
            variable.set(path)

    def _add_references(self) -> None:
        paths = filedialog.askopenfilenames(parent=self, filetypes=_IMAGE_TYPES)
        maximum = self._capabilities.max_reference_images or 4
        if len(self._references) + len(paths) > maximum:
            messagebox.showwarning(
                "Referencias Fooocus", f"El esquema admite hasta {maximum} referencias."
            )
            return
        self._references.extend(_ReferenceDraft(Path(value)) for value in paths)
        self._refresh_references()

    def _reference_index(self) -> int | None:
        selected = self._reference_tree.selection()
        return int(selected[0]) if selected else None

    def _reference_selected(self, _: object = None) -> None:
        index = self._reference_index()
        if index is None or index >= len(self._references):
            return
        value = self._references[index]
        self._reference_kind.set(_KINDS[value.kind])
        self._reference_stop.set(value.stop_at)
        self._reference_weight.set(value.weight)

    def _apply_reference(self) -> None:
        index = self._reference_index()
        if index is None:
            return
        try:
            self._references[index].kind = _KIND_VALUES[self._reference_kind.get()]
            self._references[index].stop_at = float(self._reference_stop.get())
            self._references[index].weight = float(self._reference_weight.get())
        except (KeyError, tk.TclError, ValueError) as error:
            messagebox.showerror("Referencia Fooocus", str(error))
            return
        self._refresh_references(index)

    def _remove_reference(self) -> None:
        index = self._reference_index()
        if index is not None:
            self._references.pop(index)
            self._refresh_references()

    def _toggle_reference(self) -> None:
        index = self._reference_index()
        if index is not None:
            value = self._references[index]
            value.enabled = not value.enabled
            self._refresh_references(index)

    def _move_reference(self, offset: int) -> None:
        index = self._reference_index()
        if index is None:
            return
        target = index + offset
        if 0 <= target < len(self._references):
            self._references[index], self._references[target] = (
                self._references[target],
                self._references[index],
            )
            self._refresh_references(target)

    def _refresh_references(self, selected: int | None = None) -> None:
        self._reference_tree.delete(*self._reference_tree.get_children())
        for index, value in enumerate(self._references):
            self._reference_tree.insert(
                "",
                tk.END,
                iid=str(index),
                values=(
                    "☑" if value.enabled else "☐",
                    _KINDS[value.kind],
                    f"{value.stop_at:.2f}",
                    f"{value.weight:.2f}",
                    value.path.name,
                ),
            )
        if selected is not None and selected < len(self._references):
            self._reference_tree.selection_set(str(selected))

    def _preview_reference(self) -> None:
        index = self._reference_index()
        if index is not None:
            self._preview_paths((self._references[index].path,), 0)

    def _enhancement_index(self) -> int | None:
        selected = self._enhance_tree.selection()
        return int(selected[0]) if selected else None

    def _add_enhancement(self) -> None:
        maximum = self._capabilities.max_enhancement_steps or 3
        if len(self._enhancements) >= maximum:
            messagebox.showinfo("Enhance", f"El esquema admite {maximum} etapas.")
            return
        self._enhancements.append(EnhancementStep())
        self._refresh_enhancements(len(self._enhancements) - 1)

    def _edit_enhancement(self) -> None:
        index = self._enhancement_index()
        if index is None:
            messagebox.showinfo("Enhance", "Selecciona una etapa.")
            return
        EnhancementDialog(
            self,
            self._enhancements[index],
            partial(self._save_enhancement, index),
        )

    def _save_enhancement(self, index: int, value: EnhancementStep) -> None:
        self._enhancements[index] = value
        self._refresh_enhancements(index)

    def _remove_enhancement(self) -> None:
        index = self._enhancement_index()
        if index is not None:
            self._enhancements.pop(index)
            self._refresh_enhancements()

    def _refresh_enhancements(self, selected: int | None = None) -> None:
        self._enhance_tree.delete(*self._enhance_tree.get_children())
        for index, value in enumerate(self._enhancements):
            self._enhance_tree.insert(
                "",
                tk.END,
                iid=str(index),
                values=(
                    "☑" if value.enabled else "☐",
                    value.detection_prompt or "(sin prompt)",
                    value.mask_model,
                ),
            )
        if selected is not None and selected < len(self._enhancements):
            self._enhance_tree.selection_set(str(selected))

    async def _thumbnail(self, path: Path) -> tuple[Path, Any]:
        image = await asyncio.to_thread(self._load_image, path, (190, 190))
        return path, image

    async def _gallery_thumbnails(
        self, revision: int, paths: tuple[Path, ...]
    ) -> tuple[int, tuple[tuple[Path, Any], ...]]:
        thumbnails: list[tuple[Path, Any]] = []
        for path in paths:
            thumbnails.append(await self._thumbnail(path))
        return revision, tuple(thumbnails)

    @staticmethod
    def _load_image(path: Path, size: tuple[int, int]) -> Any:
        image_module = import_module("PIL.Image")
        with image_module.open(path) as source:
            image = source.convert("RGB")
            image.thumbnail(size)
            return image.copy()

    def _add_thumbnail(self, payload: tuple[Path, Any]) -> None:
        path, image = payload
        if path not in self._gallery_paths:
            self._gallery_paths.append(path)
        photo = import_module("PIL.ImageTk").PhotoImage(image)
        self._thumbnails.append(photo)
        index = len(self._gallery_paths) - 1
        item = ttk.Frame(self._gallery, padding=4)
        item.grid(row=index // 3, column=index % 3, sticky="n")
        label = ttk.Label(item, image=photo, cursor="hand2")
        label.pack()
        label.bind("<Button-1>", partial(self._select_gallery_event, index))
        ttk.Label(item, text=path.name).pack()

    def _replace_gallery(self, paths: Sequence[Path]) -> None:
        self._gallery_revision += 1
        self._gallery_paths.clear()
        self._thumbnails.clear()
        for child in self._gallery.winfo_children():
            child.destroy()
        ordered_paths = tuple(paths)
        if ordered_paths:
            self._submit(
                self._gallery_thumbnails(self._gallery_revision, ordered_paths),
                self._add_thumbnails,
            )

    def _add_thumbnails(self, payload: tuple[int, tuple[tuple[Path, Any], ...]]) -> None:
        revision, payloads = payload
        if revision != self._gallery_revision:
            return
        for thumbnail in payloads:
            self._add_thumbnail(thumbnail)

    def _select_gallery(self, index: int) -> None:
        self._gallery_index = index
        self._preview_paths(tuple(self._gallery_paths), index)

    def _select_gallery_event(self, index: int, _: object) -> None:
        self._select_gallery(index)

    def _navigate_gallery(self, offset: int) -> None:
        if not self._gallery_paths:
            return
        self._gallery_index = (self._gallery_index + offset) % len(self._gallery_paths)
        self._preview_paths(tuple(self._gallery_paths), self._gallery_index)

    def _preview_gallery(self) -> None:
        if self._gallery_paths:
            self._preview_paths(tuple(self._gallery_paths), max(self._gallery_index, 0))

    def _preview_variable(self, variable: tk.StringVar) -> None:
        if variable.get():
            self._preview_paths((Path(variable.get()),), 0)

    def _preview_paths(self, paths: Sequence[Path], index: int) -> None:
        self._submit(
            self._preview_payload(paths[index]),
            partial(self._show_preview, tuple(paths), index),
        )

    async def _preview_payload(self, path: Path) -> tuple[Path, Any]:
        image = await asyncio.to_thread(self._load_image, path, (900, 680))
        return path, image

    def _show_preview(
        self,
        paths: tuple[Path, ...],
        index: int,
        payload: tuple[Path, Any],
    ) -> None:
        path, image = payload
        dialog = tk.Toplevel(self)
        dialog.title(f"{path.name} · {index + 1}/{len(paths)}")
        dialog.transient(self.winfo_toplevel())
        photo = import_module("PIL.ImageTk").PhotoImage(image)
        dialog._photo = photo  # type: ignore[attr-defined]
        ttk.Label(dialog, image=photo).pack(padx=8, pady=8)
        actions = ttk.Frame(dialog)
        actions.pack(pady=(0, 8))
        if len(paths) > 1:
            ttk.Button(
                actions,
                text="◀",
                command=lambda: self._replace_preview(dialog, paths, index - 1),
            ).pack(side=tk.LEFT)
            ttk.Button(
                actions,
                text="▶",
                command=lambda: self._replace_preview(dialog, paths, index + 1),
            ).pack(side=tk.LEFT, padx=(4, 0))
        ttk.Button(actions, text="Cerrar", command=dialog.destroy).pack(side=tk.LEFT, padx=(8, 0))

    def _replace_preview(self, dialog: tk.Toplevel, paths: tuple[Path, ...], index: int) -> None:
        dialog.destroy()
        self._preview_paths(paths, index % len(paths))

    def _gallery_memory_changed(self) -> None:
        self._submit(
            self._update_gallery_memory(
                self._remember_gallery.get(), tuple(self._gallery_paths)
            ),
            self._ignore_result,
        )

    async def _update_gallery_memory(self, enabled: bool, paths: tuple[Path, ...]) -> None:
        await self._service.set_gallery_memory(enabled)
        if enabled and paths:
            await self._service.remember_gallery(paths)

    def _forget_gallery(self) -> None:
        confirmed = messagebox.askyesno(
            "Galería Fooocus",
            "Se olvidarán índice y miniaturas, pero no los archivos generados.",
        )
        if not confirmed:
            return
        self._remember_gallery.set(False)
        self._replace_gallery(())
        self._submit(self._service.set_gallery_memory(False), self._ignore_result)

    def _operation_changed(self, _: object = None) -> None:
        operation = _OPERATION_VALUES.get(self._operation.get())
        hints = {
            ImageOperation.IMAGE_PROMPT: "Agrega referencias en la pestaña Referencias.",
            ImageOperation.INPAINT: "Carga una fuente y una máscara.",
            ImageOperation.OUTPAINT: "Carga una fuente y elige direcciones.",
            ImageOperation.DESCRIBE: "Carga una fuente; el prompt puede quedar vacío.",
            ImageOperation.ENHANCE: "Carga una fuente y configura las etapas Enhance.",
        }
        default = "Configura la operación y añádela a la cola."
        self._progress_text.set(hints.get(operation, default) if operation is not None else default)

    def _cancel(self) -> None:
        selection = self._queue.selection()
        if not selection:
            messagebox.showinfo("Fooocus", "Selecciona una ejecución de la cola.")
            return
        operation_id = selection[0]
        self._status.set(f"Solicitando cancelación de {operation_id[:8]}…")
        self._submit(self._service.cancel(operation_id), self._ignore_result)

    def _submit(self, coroutine: Coroutine[Any, Any, T], on_success: Callable[[T], None]) -> None:
        future = self._runner.submit(coroutine)
        future.add_done_callback(lambda completed: self._post_result(completed, on_success))

    def _post_result(self, future: Future[T], on_success: Callable[[T], None]) -> None:
        try:
            result = future.result()
        except Exception as error:
            self._post(partial(self._show_error, error))
        else:
            self._post(partial(on_success, result))

    def _show_error(self, error: BaseException) -> None:
        self._status.set("Operación Fooocus fallida")
        messagebox.showerror("Fooocus", str(error))

    @staticmethod
    def _ignore_result(_: object) -> None:
        return None

    def _post(self, callback: Callable[[], None]) -> None:
        self._callbacks.put(callback)

    def _drain_callbacks(self) -> None:
        try:
            while True:
                self._callbacks.get_nowait()()
        except Empty:
            pass
        self.after(50, self._drain_callbacks)
