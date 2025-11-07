import tkinter as tk
from tkinter import filedialog, ttk
import re
from PIL import ImageGrab, Image
import io

# --- 1. ГЛОБАЛЬНІ ЗМІННІ ТА КОНФІГУРАЦІЯ ---

# Словник для зберігання псевдокоду, розбитого по функціях
FUNCTION_CODE_MAP = {}

# Стан для відстеження перетягування об'єктів на полотні
drag_data = {"item": None, "x": 0, "y": 0, "arrow_id": None, "point_index": -1}

# Словники для відстеження зв'язків між стрілками та блоками
ARROW_CONNECTIONS = {}  # {arrow_id: {'source_tag': str, 'target_tag': str}}
BLOCK_TO_ARROWS = {}  # {block_group_tag: [arrow_id, ...]}

# Крок сітки для візуального вирівнювання (в пікселях)
GRID_SIZE = 25

# Базові розміри та відступи для елементів схеми
ZOOM_STEP_MULTIPLIER = 1.1
GLOBAL_SCALE_FACTOR_X = 1.0
GLOBAL_SCALE_FACTOR_Y = 1.0
X_CENTER_DEFAULT = 400  # Початкова X-координата центру діаграми
Y_START = 50  # Початкова Y-координата для першого блоку
BLOCK_WIDTH_DEFAULT = 200  # Базова ширина блоку
BLOCK_HEIGHT_DEFAULT = 65  # Базова (мінімальна) висота блоку
V_SPACING_DEFAULT = 50  # Базовий вертикальний відступ між блоками
BASE_H_OFFSET_DEFAULT = 270  # Базовий горизонтальний зсув для гілок (if/loop)
NEST_OFFSET_STEP_DEFAULT = 70  # Додатковий зсув для вкладених рівнів
BRANCH_V_SPACING_DEFAULT = 30  # Малий верт. відступ для початку гілки
PORT_SNAPPING_TOLERANCE = 25  # Радіус (px) для "прилипання" стрілки до порту

# Глобальні мапи для зв'язку ID, тексту та стрілок
BLOCK_TEXT_MAP = {}  # {block_group_tag: "Текст блоку"}
BLOCK_ID_COUNTER = 0  # Унікальний лічильник для ID блоків


# --- 2. УТИЛІТИ: ЗБЕРЕЖЕННЯ ТА ЕКСПОРТ ---

def _toggle_grid(canvas, is_visible):
    """Приховує або показує лінії сітки на полотні."""
    GRID_TAG = "grid_line"
    state = 'normal' if is_visible else 'hidden'

    # Змінюємо стан видимості для всіх ліній з тегом GRID_TAG
    canvas.itemconfig(GRID_TAG, state=state)

    # При появі, сітка має бути на задньому плані
    if is_visible:
        canvas.tag_lower(GRID_TAG)


def save_full_flowchart_as_png_via_pil(canvas, filepath):
    """
    Експортує *повний* вміст полотна (всі елементи) у файл PNG.

    Використовує PostScript для захоплення всієї сцени, а не лише видимої
    частини, та ігнорує сітку при розрахунку меж.
    """
    ps_data = None
    MIN_PADDING_PX = 50  # Мінімальний відступ
    PADDING_FACTOR = 0.05  # 5% відступ від розміру вмісту

    try:
        # 1. Отримуємо межі всіх значущих елементів (блоки та стрілки).
        canvas.update_idletasks()

        # Ігноруємо сітку, порти та інші допоміжні елементи.
        content_items = canvas.find_withtag("block") + canvas.find_withtag("flow_arrow")

        if not content_items:
            print("Помилка: Полотно порожнє. Нічого зберігати.")
            return False

        bbox_initial = canvas.bbox(*content_items)

        if not bbox_initial:
            print("Помилка: Блоки/стрілки не знайдені.")
            return False

        x0, y0, x1, y1 = bbox_initial

        # 2. Розрахунок динамічних відступів (padding) для PNG.
        width = x1 - x0
        height = y1 - y0

        dynamic_padding = int(max(width, height) * PADDING_FACTOR)
        final_padding = max(MIN_PADDING_PX, dynamic_padding)

        # 3. Визначення області захоплення PostScript.

        # Координати верхнього лівого кута області захоплення.
        x_start_coord = x0 - final_padding
        y_start_coord = y0 - final_padding

        # Загальна ширина та висота області захоплення.
        final_width = width + final_padding * 2
        final_height = height + final_padding * 2

        # Тимчасово ховаємо сітку перед експортом
        _toggle_grid(canvas, False)

        # 4. Генеруємо PostScript у пам'яті (в RAM).
        ps_data = canvas.postscript(
            colormode='color',
            x=x_start_coord,
            y=y_start_coord,
            width=final_width,
            height=final_height
        )

        # 5. Конвертуємо PostScript дані в об'єкт Image (Pillow).
        img = Image.open(io.BytesIO(ps_data.encode('utf-8')))

        # 6. Зберігаємо зображення у файл.
        img.save(filepath, "PNG")

        print(f"✅ Повна діаграма успішно збережена у форматі PNG: {filepath}")
        return True, _toggle_grid(canvas, True)  # Повертаємо сітку

    except Exception as e:
        print(f"❌ Фатальна помилка при експорті PNG через PIL: {e}")
        return False, _toggle_grid(canvas, True)  # Повертаємо сітку у разі помилки


def save_canvas_screenshot(canvas, filepath):
    """Зберігає *лише видиму* частину полотна (скріншот вікна)."""
    if ImageGrab is None:
        print("❌ Помилка: Pillow (PIL) не встановлено. Збереження PNG неможливе.")
        return False
    try:
        canvas.update_idletasks()
        x = canvas.winfo_rootx()
        y = canvas.winfo_rooty()
        x1 = x + canvas.winfo_width()
        y1 = y + canvas.winfo_height()
        img = ImageGrab.grab(bbox=(x, y, x1, y1))
        img.save(filepath, "PNG")
        print(f"✅ Видима діаграма збережена у форматі PNG: {filepath}")
        return True
    except Exception as e:
        print(f"❌ Помилка при збереженні PNG (ImageGrab): {e}")
        return False


def _draw_ports_for_block(canvas, x0, y0, x1, y1, group_tag, is_rhombus=False):
    """
    Створює невидимі "порти" (точки прив'язки) для блоку.

    Ці порти використовуються для логіки "прилипання" стрілок.
    """
    W = x1 - x0
    H = y1 - y0
    center_x = (x0 + x1) / 2
    center_y = (y0 + y1) / 2

    # Визначення координат портів.
    port_coords = []

    if is_rhombus:
        # Ромби (if/while) мають 4 порти (T, L, R, B).
        port_coords = [
            (center_x, y0),  # Вхід (зверху)
            (x0, center_y),  # Вихід 'False' (зліва)
            (x1, center_y),  # Вихід 'True' (справа)
            (center_x, y1)  # З'єднання (знизу)
        ]
    else:
        # Інші блоки (rect, ellipse) мають 2 порти (T, B).
        port_coords = [
            (center_x, y0),  # Вхід (зверху)
            (center_x, y1)  # Вихід (знизу)
        ]

    PORT_RADIUS = 3  # Радіус зони "прилипання" для порту.

    for px, py in port_coords:
        # Створюємо невидимий об'єкт-порт.
        canvas.create_oval(
            px - PORT_RADIUS, py - PORT_RADIUS,
            px + PORT_RADIUS, py + PORT_RADIUS,
            fill="", outline="", width=0,  # fill="" та outline="" роблять овал невидимим.
            tags=("block_port", group_tag)
        )


# --- 3. ДОПОМІЖНІ ФУНКЦІЇ МАЛЮВАННЯ (ПРИМІТИВИ) ---

def draw_arrow(canvas, x_start, y_start, x_end, y_end, draw_arrow_head=True):
    """Малює просту пряму стрілку з однієї точки в іншу."""
    if draw_arrow_head:
        canvas.create_line(x_start, y_start, x_end, y_end, arrow=tk.LAST, width=2, tags=("flow_arrow",))
    else:
        canvas.create_line(x_start, y_start, x_end, y_end, width=2, tags=("flow_arrow",))


def draw_multi_point_arrow(canvas, points, text="", draw_arrow_head=True):
    """Малює ламану стрілку, що проходить через список точок (points)."""
    if draw_arrow_head:
        canvas.create_line(points, arrow=tk.LAST, width=2, tags=("flow_arrow",))
    else:
        canvas.create_line(points, width=2, tags=("flow_arrow",))

    # Додавання тексту ("True", "False") біля першого сегмента стрілки.
    if text:
        if len(points) > 1:
            p1 = points[0]
            p2 = points[1]
            # Визначаємо позицію тексту залежно від орієнтації лінії.
            if p1[0] == p2[0]:  # Вертикальна лінія
                x_pos = p1[0] - 10
                y_pos = (p1[1] + p2[1]) / 2
                anchor = "e"  # (east - праворуч від тексту)
            elif p1[1] == p2[1]:  # Горизонтальна лінія
                x_pos = (p1[0] + p2[0]) / 2
                y_pos = p1[1] - 10
                anchor = "s"  # (south - під текстом)
            else:  # Діагональна (запасний варіант)
                x_pos = (p1[0] + p2[0]) / 2
                y_pos = (p1[1] + p2[1]) / 2 - 10
                anchor = "s"

            canvas.create_text(x_pos, y_pos, text=text, font=("Arial", 9, "bold"), fill="black", anchor=anchor)


def draw_grid_lines(canvas, grid_size, max_size, is_visible):
    """Малює або оновлює сітку на полотні."""
    GRID_COLOR = "#cccccc"  # Світло-сірий
    GRID_TAG = "grid_line"
    state = 'normal' if is_visible else 'hidden'

    # Видаляємо стару сітку перед малюванням нової.
    canvas.delete(GRID_TAG)

    # Вертикальні лінії
    for i in range(0, max_size, grid_size):
        canvas.create_line(i, 0, i, max_size, fill=GRID_COLOR, tags=(GRID_TAG,), dash=(1, 2), state=state)

    # Горизонтальні лінії
    for j in range(0, max_size, grid_size):
        canvas.create_line(0, j, max_size, j, fill=GRID_COLOR, tags=(GRID_TAG,), dash=(1, 2), state=state)

    # Переміщуємо сітку на задній план, під усі блоки.
    canvas.tag_lower(GRID_TAG)


def draw_ellipse(canvas, x, y_top, text, h_scale, v_scale, color):
    """Малює блок "Початок/Кінець" (Овал)."""
    W = BLOCK_WIDTH_DEFAULT * h_scale
    TEXT_PADDING = 15  # Більший відступ для овалу
    MIN_H = BLOCK_HEIGHT_DEFAULT * v_scale

    # Унікальний тег групи для цього блоку
    group_tag = f"ell_{int(x)}_{int(y_top)}"
    # Обмеження ширини тексту
    text_width_constraint = W - (TEXT_PADDING * 2.5)

    # 1. Створюємо текст (для розрахунку висоти)
    text_id = canvas.create_text(
        x, y_top + TEXT_PADDING, text=text, font=("Arial", int(11 * v_scale), "bold"), width=text_width_constraint,
        anchor="n"
    )
    # 2. Розраховуємо реальну висоту блоку
    text_bbox = canvas.bbox(text_id)
    text_height = 0 if not text_bbox else (text_bbox[3] - text_bbox[1])
    H = max(MIN_H, text_height + (TEXT_PADDING * 2))

    # 3. Координати овалу
    x0 = x - W / 2
    y0 = y_top
    x1 = x + W / 2
    y1 = y_top + H

    # 4. Малюємо овал
    canvas.create_oval(x0, y0, x1, y1, fill=color, outline="black", tags=("block", "ellipse", group_tag))

    # 5. Центруємо текст у готовому блоці
    canvas.coords(text_id, x, y_top + H / 2)
    canvas.itemconfigure(text_id, anchor="center")
    canvas.tag_raise(text_id)  # Текст поверх фігури
    canvas.itemconfig(text_id, tags=canvas.gettags(text_id) + ("block_text", group_tag))

    # 6. Малюємо невидимі порти прив'язки
    _draw_ports_for_block(canvas, x0, y0, x1, y1, group_tag)

    return (x, y1)  # Повертаємо координати нижньої точки


def draw_rectangle(canvas, x, y_top, text, h_scale, v_scale, color):
    """Малює стандартний блок операції (Прямокутник)."""
    W = BLOCK_WIDTH_DEFAULT * h_scale
    TEXT_PADDING = 10
    MIN_H = BLOCK_HEIGHT_DEFAULT * v_scale

    group_tag = f"rect_{int(x)}_{int(y_top)}"
    text_width_constraint = W - (TEXT_PADDING * 2)

    # 1. Створюємо текст
    text_id = canvas.create_text(
        x, y_top + TEXT_PADDING, text=text, font=("Arial", int(14 * v_scale)), width=text_width_constraint, anchor="n"
    )
    # 2. Розраховуємо висоту
    text_bbox = canvas.bbox(text_id)
    text_height = 0 if not text_bbox else (text_bbox[3] - text_bbox[1])
    H = max(MIN_H, text_height + (TEXT_PADDING * 2))

    # 3. Координати
    x0 = x - W / 2
    y0 = y_top
    x1 = x + W / 2
    y1 = y_top + H

    # 4. Малюємо прямокутник
    canvas.create_rectangle(x0, y0, x1, y1, fill=color, outline="black", tags=("block", "rect", group_tag))

    # 5. Центруємо текст
    canvas.coords(text_id, x, y_top + H / 2)
    canvas.itemconfigure(text_id, anchor="center")
    canvas.tag_raise(text_id)
    canvas.itemconfig(text_id, tags=canvas.gettags(text_id) + ("block_text", group_tag))

    # 6. Малюємо порти
    _draw_ports_for_block(canvas, x0, y0, x1, y1, group_tag)

    return (x, y1)


def draw_rhombus(canvas, x, y_top, text, h_scale, v_scale, color):
    """Малює блок умови або циклу (Ромб)."""
    TEXT_PADDING = 20
    MIN_H = BLOCK_HEIGHT_DEFAULT * v_scale
    H = MIN_H
    h = H / 2
    y_center = y_top + h
    text = text.replace("and", "і").replace("or", "або")

    group_tag = f"rhombus_{int(x)}_{int(y_top)}"

    # 1. Розраховуємо ширину ромба на основі довжини тексту
    try:
        f = tk.font.Font(family="Arial", size=int(14 * v_scale));
        text_width = f.measure(text)
    except Exception:
        text_width = 100

    W_base = BLOCK_WIDTH_DEFAULT
    W_for_text = (text_width + (TEXT_PADDING * 1.5)) * 2.2  # Емпіричний коефіцієнт для ромба
    W = max(W_base, W_for_text) * h_scale
    w = W / 2

    # 2. Координати вершин ромба (p1...p4)
    p1 = (x - w, y_center);  # Ліва
    p2 = (x, y_top + H);  # Нижня
    p3 = (x + w, y_center);  # Права
    p4 = (x, y_top)  # Верхня

    # 3. Малюємо ромб
    canvas.create_polygon(p1, p2, p3, p4, fill=color, outline="black", tags=("block", "rhombus", group_tag))

    # 4. Малюємо текст
    text_width_constraint = W - (W / 2) - TEXT_PADDING + 100
    text_id = canvas.create_text(x, y_center, text=text, font=("Arial", int(14 * v_scale)), width=text_width_constraint,
                                 anchor="center")
    canvas.tag_raise(text_id)
    canvas.itemconfig(text_id, tags=canvas.gettags(text_id) + ("block_text", group_tag))

    # 5. Малюємо порти (спеціальний режим для ромба)
    _draw_ports_for_block(canvas, x - w, y_top, x + w, y_top + H, group_tag, is_rhombus=True)

    # Повертаємо словник з ключовими точками для стрілок
    return {"top": p4, "bottom": p2, "left": p1, "right": p3}


def draw_subroutine(canvas, x, y_top, text, h_scale, v_scale, color):
    """Малює блок виклику підпрограми (Прямокутник з лініями)."""
    W = BLOCK_WIDTH_DEFAULT * h_scale
    TEXT_PADDING = 10
    MIN_H = BLOCK_HEIGHT_DEFAULT * v_scale
    LINE_OFFSET = 15 * h_scale  # Відступ для внутрішніх ліній

    sub_tag = f"sub_{int(x)}_{int(y_top)}"
    text_width_constraint = W - (TEXT_PADDING * 2) - (LINE_OFFSET * 2)

    # 1. Текст та розрахунок висоти
    text_id = canvas.create_text(
        x, y_top + TEXT_PADDING, text=text, font=("Arial", int(14 * v_scale)), width=text_width_constraint, anchor="n"
    )
    text_bbox = canvas.bbox(text_id)
    text_height = 0 if not text_bbox else (text_bbox[3] - text_bbox[1])
    H = max(MIN_H, text_height + (TEXT_PADDING * 2))

    # 2. Координати
    x0 = x - W / 2;
    x1 = x + W / 2;
    y0 = y_top;
    y1 = y_top + H

    # 3. Малюємо основний прямокутник
    canvas.create_rectangle(x0, y0, x1, y1, fill=color, outline="black", tags=("block", "sub", sub_tag))

    # 4. Малюємо додаткові вертикальні лінії
    canvas.create_line(x0 + LINE_OFFSET, y0, x0 + LINE_OFFSET, y1, width=1, fill="black", tags=(sub_tag,))
    canvas.create_line(x1 - LINE_OFFSET, y0, x1 - LINE_OFFSET, y1, width=1, fill="black", tags=(sub_tag,))

    # 5. Центруємо текст
    canvas.coords(text_id, x, y_top + H / 2)
    canvas.itemconfigure(text_id, anchor="center")
    canvas.tag_raise(text_id)
    canvas.itemconfig(text_id, tags=canvas.gettags(text_id) + ("block_text", sub_tag))

    # 6. Малюємо порти
    _draw_ports_for_block(canvas, x0, y0, x1, y1, sub_tag)

    return (x, y1)


def draw_parallelogram(canvas, x, y_top, text, h_scale, v_scale, color):
    """Малює блок Вводу/Виводу (Паралелограм)."""
    W = BLOCK_WIDTH_DEFAULT * h_scale
    TEXT_PADDING = 10
    MIN_H = BLOCK_HEIGHT_DEFAULT * v_scale
    skew_offset = (BLOCK_WIDTH_DEFAULT / 8) * h_scale  # Горизонтальний зсув для нахилу

    group_tag = f"para_{int(x)}_{int(y_top)}"
    text_width_constraint = W - (TEXT_PADDING * 2) - skew_offset

    # 1. Текст та розрахунок висоти
    text_id = canvas.create_text(x, y_top + TEXT_PADDING, text=text, font=("Arial", int(14 * v_scale)),
                                 width=text_width_constraint, anchor="n")
    text_bbox = canvas.bbox(text_id)
    text_height = 0 if not text_bbox else (text_bbox[3] - text_bbox[1])
    H = max(MIN_H, text_height + (TEXT_PADDING * 2))

    w_half = W / 2;
    h_half = H / 2;
    y_center = y_top + h_half

    # 2. Координати вершин (p1...p4)
    p1 = (x - w_half + skew_offset, y_top);  # Верхня ліва
    p2 = (x + w_half + skew_offset, y_top);  # Верхня права
    p3 = (x + w_half - skew_offset, y_top + H);  # Нижня права
    p4 = (x - w_half - skew_offset, y_top + H)  # Нижня ліва

    # 3. Малюємо фігуру
    canvas.create_polygon(p1, p2, p3, p4, fill=color, outline="black", tags=("block", "para", group_tag))

    # 4. Центруємо текст
    canvas.coords(text_id, x, y_center)
    canvas.itemconfigure(text_id, anchor="center")
    canvas.tag_raise(text_id)
    canvas.itemconfig(text_id, tags=canvas.gettags(text_id) + ("block_text", group_tag))

    # 5. Малюємо порти (використовуючи зовнішні межі фігури)
    x0_bounds = x - w_half - skew_offset;
    x1_bounds = x + w_half + skew_offset;
    _draw_ports_for_block(canvas, x0_bounds, y_top, x1_bounds, y_top + H, group_tag)

    return (x, y_top + H)


def draw_hexagon(canvas, x, y_top, text, h_scale, v_scale, color):
    """Малює блок циклу 'for' (Шестикутник)."""
    TEXT_PADDING = 10
    MIN_H = BLOCK_HEIGHT_DEFAULT * v_scale
    H = MIN_H
    h = H / 2;
    y_center = y_top + h

    group_tag = f"hex_{int(x)}_{int(y_top)}"

    # 1. Розрахунок ширини на основі тексту
    try:
        f = tk.font.Font(family="Arial", size=int(14 * v_scale));
        text_width = f.measure(text)
    except Exception:
        text_width = 100

    W_base = BLOCK_WIDTH_DEFAULT;
    W_for_text = (text_width + (TEXT_PADDING * 2)) * 2.1  # Емпіричний коефіцієнт
    W = max(W_base, W_for_text) * h_scale * 1.3
    w = W / 2
    hex_offset = H  # Зсув для бічних граней

    # 2. Координати вершин (p1...p6)
    p1 = (x - w + hex_offset, y_top);  # Верхня ліва
    p2 = (x + w - hex_offset, y_top);  # Верхня права
    p3 = (x + w, y_center);  # Права
    p4 = (x + w - hex_offset, y_top + H);  # Нижня права
    p5 = (x - w + hex_offset, y_top + H);  # Нижня ліва
    p6 = (x - w, y_center)  # Ліва

    # 3. Малюємо фігуру
    canvas.create_polygon(p1, p2, p3, p4, p5, p6, fill=color, outline="black",
                          tags=("block", "hex", group_tag))
    # 4. Малюємо текст
    text_width_constraint = W - (2 * hex_offset) - (2 * TEXT_PADDING) + 100
    text_id = canvas.create_text(x, y_center, text=text, font=("Arial", int(14 * v_scale)), width=text_width_constraint,
                                 anchor="center")
    canvas.tag_raise(text_id)
    canvas.itemconfig(text_id, tags=canvas.gettags(text_id) + ("block_text", group_tag))

    # 5. Малюємо порти
    x0_bounds = x - w;
    x1_bounds = x + w;
    _draw_ports_for_block(canvas, x0_bounds, y_top, x1_bounds, y_top + H, group_tag)

    # Повертаємо ключові точки
    return {"top": (x, y_top), "bottom": (x, y_top + H), "left": p6, "right": p3}


# --- 4. ДОПОМІЖНІ ФУНКЦІЇ: АНАЛІЗ ЛОГІЧНИХ БЛОКІВ ---

def find_if_branches(code_list, start_index):
    """
    Знаходить тіло гілки 'If' (true_branch), тіло гілки 'Else' (false_branch)
    та індекс рядка, де закінчується вся конструкція ('Все якщо').

    Враховує вкладеність, щоб не зупинитись на 'Все якщо' від вкладеного 'If'.
    """
    true_branch_code = []
    false_branch_code = []
    if_end_index = -1  # Індекс рядка "Все якщо"
    else_index = -1  # Індекс рядка "Інакше"
    nested_if_balance = 0  # Лічильник вкладеності
    j = start_index + 1

    while j < len(code_list):
        line_j = code_list[j].strip()

        # 1. Відстеження вкладеності
        if (line_j.startswith("Якщо:") or
                line_j.startswith("Повторити для:") or
                line_j.startswith("Повторити поки:") or
                line_j.startswith("Повторити доки (початок)")):
            nested_if_balance += 1

        # 2. Пошук кінця поточного блоку 'If'
        elif line_j.startswith("Все якщо"):
            if nested_if_balance == 0:
                if_end_index = j  # Знайшли кінець *нашого* блоку
                break
            else:
                nested_if_balance -= 1  # Це кінець вкладеного блоку

        # 3. Відстеження кінця вкладених циклів
        elif line_j.startswith("Все повторити"):
            if nested_if_balance > 0:
                nested_if_balance -= 1  # (Тільки якщо ми всередині циклу)

        # 4. Пошук 'Інакше' (тільки на нашому рівні вкладеності)
        elif (line_j.startswith("Інакше Якщо:") or line_j.startswith("Інакше")) \
                and nested_if_balance == 0 and else_index == -1:
            else_index = j

        j += 1

    # Якщо "Все якщо" не знайдено, блок триває до кінця коду
    if if_end_index == -1:
        if_end_index = len(code_list)

    # 5. Розділення коду на гілки 'True' та 'False'
    if else_index != -1:
        # Є гілка 'Інакше'
        true_branch_code = code_list[start_index + 1: else_index]
        false_branch_code = code_list[else_index: if_end_index]
    else:
        # Немає гілки 'Інакше'
        true_branch_code = code_list[start_index + 1: if_end_index]
        false_branch_code = []

    return true_branch_code, false_branch_code, if_end_index


def find_loop_body(code_list, start_index):
    """
    Знаходить тіло циклу (loop_body_code) та індекс рядка
    "Все повторити", що завершує цей цикл.

    Враховує вкладеність (if, for, while).
    """
    loop_body_code = []
    loop_end_index = -1
    end_marker = "Все повторити"  # Маркер кінця для 'for' та 'while'

    # Перевірка, чи це дійсно початок циклу
    if not (code_list[start_index].strip().startswith("Повторити для:") or \
            code_list[start_index].strip().startswith("Повторити поки:")):
        return [], start_index

    nested_balance = 0  # Лічильник вкладеності
    j = start_index + 1

    while j < len(code_list):
        line_j = code_list[j].strip()

        # 1. Відстеження вкладених блоків
        if (line_j.startswith("Якщо:") or
                line_j.startswith("Повторити для:") or
                line_j.startswith("Повторити поки:") or
                line_j.startswith("Повторити доки (початок)")):
            nested_balance += 1

        # 2. Пошук кінця *нашого* циклу
        elif line_j.startswith(end_marker):
            if nested_balance == 0:
                loop_end_index = j  # Знайшли кінець
                break
            else:
                nested_balance -= 1  # Це кінець вкладеного циклу

        # 3. Відстеження кінця вкладених 'If'
        elif line_j.startswith("Все якщо"):
            if nested_balance > 0:
                nested_balance -= 1

        j += 1

    # Якщо кінець не знайдено, цикл триває до кінця файлу
    if loop_end_index == -1:
        loop_end_index = len(code_list) - 1

    # 4. Вилучення тіла циклу
    loop_body_code = code_list[start_index + 1: loop_end_index]
    return loop_body_code, loop_end_index


# --- 5. ДОПОМІЖНА ФУНКЦІЯ: ВИЛУЧЕННЯ ТОКЕНІВ З ДУЖОК ---

def get_block_tokens(word_list, start_index):
    """
    Витягує список токенів, що містяться між { та }.

    Знаходить відповідну закриваючу дужку '}', враховуючи вкладеність.
    Повертає (список_токенів_всередині, індекс_закриваючої_дужки).
    """
    balance = 0  # Баланс дужок
    end_index = -1

    if start_index >= len(word_list) or word_list[start_index] != '{':
        return [], -1  # Помилка: починається не з '{'

    for i in range(start_index, len(word_list)):
        if word_list[i] == '{':
            balance += 1
        elif word_list[i] == '}':
            balance -= 1

        if balance == 0:
            end_index = i  # Знайшли відповідну '}'
            break

    # Повертаємо токени *між* дужками
    return word_list[start_index + 1: end_index], end_index


# --- 6. ОСНОВНА ЛОГІКА МАЛЮВАННЯ ДІАГРАМИ ---

def draw_flowchart(canvas, code_list, h_scale, v_scale, loop_offset_factor, if_offset_factor, colors):
    """
    (Ця функція більше не використовується, замінена на draw_flowchart_with_offset)
    Обгортка для запуску рекурсивного малювання.
    """
    canvas.delete("all")
    EXTENDED_SIZE = 4000
    canvas.config(scrollregion=(0, 0, EXTENDED_SIZE, EXTENDED_SIZE))

    _draw_flowchart_recursive(canvas, code_list, Y_START, EXTENDED_SIZE / 2, h_scale, v_scale, loop_offset_factor,
                              if_offset_factor, colors, skip_init=False, nesting_level=0)

    canvas.update_idletasks()
    actual_bbox = canvas.bbox("all")
    if actual_bbox:
        canvas.config(scrollregion=(
            actual_bbox[0] - 50,
            actual_bbox[1] - 50,
            actual_bbox[2] + 50,
            actual_bbox[3] + 50
        ))
    else:
        canvas.config(scrollregion=(0, 0, 800, 800))


def _draw_flowchart_recursive(canvas, code_list, start_y, x_center, h_scale, v_scale, loop_offset_factor,
                              if_offset_factor, colors, skip_init, nesting_level=0):
    """
    Рекурсивно малює блок-схему на основі списку псевдокоду.

    Повертає (кінцевий_y, кінцевий_x) - координати точки,
    з якої має виходити наступна стрілка.
    """
    global BLOCK_TEXT_MAP
    global BLOCK_ID_COUNTER

    current_y = start_y  # Поточна Y-координата (низ останнього блоку)
    last_connector_x = x_center  # X-координата для з'єднання
    last_connector_y = start_y  # Y-координата для з'єднання

    # Спеціальна змінна для циклу do-while (для малювання стрілки назад)
    start_do_body_y = start_y

    # Прапор, що запобігає малюванню зайвої стрілки (напр., після 'do-while')
    skip_next_connecting_arrow = False

    # 1. Розпакування кольорів
    try:
        (color_ellipse, color_rect, color_rhombus, color_sub, color_hex) = colors
    except Exception:
        # Резервні кольори, якщо щось пішло не так
        color_ellipse = "#FFD1DC"
        color_rect = "#ADD8E6"
        color_rhombus = "#FFFFE0"
        color_sub = "#CCEEFF"
        color_hex = "#D8BFD8"

    # 2. Розрахунок масштабованих відступів
    W = BLOCK_WIDTH_DEFAULT * h_scale
    H = BLOCK_HEIGHT_DEFAULT * v_scale
    V_SP = V_SPACING_DEFAULT * v_scale
    BASE_HO = BASE_H_OFFSET_DEFAULT * h_scale
    NEST_OS = NEST_OFFSET_STEP_DEFAULT * h_scale
    BRANCH_VS = BRANCH_V_SPACING_DEFAULT * v_scale

    i = 0
    while i < len(code_list):
        line = code_list[i].strip()

        # 3. Пропуск прапорця 'skip_init'
        if skip_init and line.startswith("Ініціалізація:"):
            i += 1
            continue

        try:
            # 4. Пропуск порожніх рядків або маркерів кінця блоків
            if (not line or
                    line.startswith("Все") or
                    line.startswith("Завершення:") or
                    line.startswith("Все повторити")):
                i += 1
                continue

            # (Рядок 'Інакше' обробляється всередині 'find_if_branches')
            if line.strip() == "Інакше":
                i += 1
                continue

            # 5. Розрахунок Y-координати верхівки *поточного* блоку
            block_top_y = current_y
            if current_y != start_y:
                block_top_y += V_SP  # Додаємо вертикальний відступ

            # 6. Малювання з'єднувальної стрілки (від попереднього блоку до поточного)
            if current_y != start_y:
                if not skip_next_connecting_arrow:
                    draw_arrow(canvas, last_connector_x, last_connector_y, x_center, block_top_y, draw_arrow_head=True)
                skip_next_connecting_arrow = False

            # === 7. ОБРОБКА БЛОКІВ ===

            # --- 7.1. Цикл "DO-WHILE" (початок тіла) ---
            if line == "Повторити доки (початок)":
                start_do_body_y = block_top_y  # Запам'ятовуємо Y тіла

                # Знаходимо кінець тіла (рядок "Повторити доки (умова):")
                body_end_index = -1
                j = i + 1
                while j < len(code_list):
                    if code_list[j].strip().startswith("Повторити доки (умова):"):
                        body_end_index = j
                        break
                    j += 1
                if body_end_index == -1:
                    raise ValueError("Malformed do-while block: condition not found")

                loop_body_code = code_list[i + 1: body_end_index]

                # Малюємо стрілку до тіла (якщо це не перший блок)
                if current_y != start_y:
                    draw_arrow(canvas, last_connector_x, last_connector_y, x_center, start_do_body_y,
                               draw_arrow_head=True)

                # Рекурсивний виклик для малювання тіла циклу
                (body_end_y, body_end_x) = _draw_flowchart_recursive(canvas, loop_body_code, start_do_body_y, x_center,
                                                                     h_scale, v_scale, loop_offset_factor,
                                                                     if_offset_factor, colors, skip_init,
                                                                     nesting_level + 1)
                # Оновлюємо координати для наступного блоку (умови)
                last_connector_y = body_end_y
                last_connector_x = body_end_x
                current_y = body_end_y
                i = body_end_index  # Перестрибуємо на рядок умови
                skip_next_connecting_arrow = True  # З'єднувальна стрілка вже намальована
                continue

            # --- 7.2. Цикл "DO-WHILE" (умова) ---
            elif line.startswith("Повторити доки (умова):"):
                text = line.replace("Повторити доки (умова): ", "")
                block_top_y = current_y + V_SP
                draw_arrow(canvas, last_connector_x, last_connector_y, x_center, block_top_y, draw_arrow_head=True)

                rhombus_coords = draw_rhombus(canvas, x_center, block_top_y, text, h_scale, v_scale, color_rhombus)

                # Збереження тексту для експорту
                group_tag = f"rhombus_{BLOCK_ID_COUNTER}"
                BLOCK_TEXT_MAP[group_tag] = text
                BLOCK_ID_COUNTER += 1

                # Малювання стрілки "True" (назад до тіла циклу)
                body_nesting_level = nesting_level + 1
                current_loop_offset_back = (BASE_HO / 3) + (body_nesting_level * NEST_OS * loop_offset_factor)
                back_bend_x = rhombus_coords["left"][0] - current_loop_offset_back
                p1_back = rhombus_coords["left"]
                p2_back = (back_bend_x, p1_back[1])
                P_BACK_Y = start_do_body_y - V_SP / 2
                p3_back = (back_bend_x, P_BACK_Y)
                p4_back = (x_center, P_BACK_Y)
                draw_multi_point_arrow(canvas, [p1_back, p2_back, p3_back, p4_back], text="True", draw_arrow_head=True)

                # Малювання стрілки "False" (вихід з циклу)
                start_exit_x, start_exit_y = rhombus_coords["bottom"]
                join_exit_y = start_exit_y + V_SP
                draw_arrow(canvas, start_exit_x, start_exit_y, x_center, join_exit_y,
                           draw_arrow_head=True)

                current_y = join_exit_y
                last_connector_x = x_center
                last_connector_y = join_exit_y
                i += 1
                continue

            # --- 7.3. Цикл "FOR" ---
            if line.startswith("Повторити для:"):
                text = line.replace("Повторити для: ", "")

                hex_coords = draw_hexagon(canvas, x_center, block_top_y, text, h_scale, v_scale, color_hex)

                group_tag = f"hex_{BLOCK_ID_COUNTER}"
                BLOCK_TEXT_MAP[group_tag] = text
                BLOCK_ID_COUNTER += 1

                # Знаходимо тіло та кінець циклу
                loop_body_code, loop_end_index = find_loop_body(code_list, i)

                # Стрілка до тіла циклу
                branch_start_y = hex_coords["bottom"][1] + BRANCH_VS
                draw_arrow(canvas, hex_coords["bottom"][0], hex_coords["bottom"][1],
                           x_center, branch_start_y, draw_arrow_head=True)

                # Рекурсивне малювання тіла
                (body_end_y, body_end_x) = _draw_flowchart_recursive(canvas, loop_body_code, branch_start_y, x_center,
                                                                     h_scale, v_scale, loop_offset_factor,
                                                                     if_offset_factor, colors, skip_init,
                                                                     nesting_level + 1)

                # Малювання стрілки "назад" (від кінця тіла до входу в шестикутник)
                if nesting_level == 0:
                    current_loop_offset_back = (BASE_HO / 3) + (NEST_OS * 10 * loop_offset_factor + 30)
                else:
                    current_loop_offset_back = (BASE_HO / 3) + (nesting_level * NEST_OS * 10 * loop_offset_factor)

                loop_back_x = hex_coords["left"][0] - current_loop_offset_back
                p1_back = (body_end_x, body_end_y)
                p2_back = (body_end_x, body_end_y + V_SP / 2)
                p3_back = (loop_back_x, body_end_y + V_SP / 2)
                p4_back = (loop_back_x, hex_coords["left"][1])
                p5_back = hex_coords["left"]
                draw_multi_point_arrow(canvas, [p1_back, p2_back, p3_back, p4_back, p5_back], draw_arrow_head=True)

                # Малювання стрілки "вихід" (від правої грані)
                start_exit_x, start_exit_y = hex_coords["right"]
                join_exit_y = body_end_y + V_SP / 1
                if nesting_level == 0:
                    current_loop_offset_exit = (BASE_HO / 3) + (NEST_OS * loop_offset_factor + 50)
                else:
                    current_loop_offset_exit = (BASE_HO / 3) + (nesting_level * NEST_OS * loop_offset_factor)

                EXIT_OFFSET_X = current_loop_offset_exit + NEST_OS * 2
                final_join_y = join_exit_y + V_SP / 2
                exit_points = [
                    (start_exit_x, start_exit_y),
                    (start_exit_x + EXIT_OFFSET_X, start_exit_y),
                    (start_exit_x + EXIT_OFFSET_X, final_join_y),
                    (x_center, final_join_y),
                ]

                # (Логіка для виходу з циклу на 0-му рівні вкладеності)
                if nesting_level == 0:
                    exit_points = [
                        (start_exit_x, start_exit_y),
                        (start_exit_x + EXIT_OFFSET_X, start_exit_y),
                        (start_exit_x + EXIT_OFFSET_X, final_join_y),
                        (x_center, final_join_y),
                        (x_center, final_join_y + V_SP)]
                    draw_multi_point_arrow(canvas, exit_points, draw_arrow_head=True)
                else:
                    draw_multi_point_arrow(canvas, exit_points, draw_arrow_head=False)

                current_y = final_join_y
                last_connector_y = final_join_y
                last_connector_x = x_center
                skip_next_connecting_arrow = True
                i = loop_end_index + 1  # Перестрибуємо в кінець циклу
                continue

            # --- 7.4. Цикл "WHILE" ---
            elif line.startswith("Повторити поки:"):
                text = line.replace("Повторити поки: ", "")

                rhombus_coords = draw_rhombus(canvas, x_center, block_top_y, text, h_scale, v_scale, color_rhombus)

                group_tag = f"rhombus_{BLOCK_ID_COUNTER}"
                BLOCK_TEXT_MAP[group_tag] = text
                BLOCK_ID_COUNTER += 1

                loop_body_code, loop_end_index = find_loop_body(code_list, i)

                # Стрілка "True" (до тіла циклу)
                branch_start_y = rhombus_coords["bottom"][1] + BRANCH_VS
                p1_true = rhombus_coords["bottom"]
                p2_true = (x_center, branch_start_y)
                draw_multi_point_arrow(canvas, [p1_true, p2_true], text="True", draw_arrow_head=True)

                # Рекурсивне малювання тіла
                (body_end_y, body_end_x) = _draw_flowchart_recursive(canvas, loop_body_code, branch_start_y, x_center,
                                                                     h_scale, v_scale, loop_offset_factor,
                                                                     if_offset_factor, colors, skip_init,
                                                                     nesting_level + 1)

                # Стрілка "назад" (від кінця тіла до умови)
                current_loop_offset_back = (BASE_HO / 3) + (nesting_level * NEST_OS * loop_offset_factor)
                loop_back_x = rhombus_coords["left"][0] - current_loop_offset_back * 8 * loop_offset_factor
                p1_back = (body_end_x, body_end_y)
                p2_back = (body_end_x, body_end_y + V_SP / 1)
                p3_back = (loop_back_x, body_end_y + V_SP / 1)
                p4_back = (loop_back_x, rhombus_coords["top"][1] - 20 * v_scale)
                p5_back = (body_end_x, rhombus_coords["top"][1] - 20 * v_scale)
                draw_multi_point_arrow(canvas, [p1_back, p2_back, p3_back, p4_back, p5_back], draw_arrow_head=True)

                # Стрілка "False" (вихід з циклу)
                start_exit_x, start_exit_y = rhombus_coords["right"]
                join_exit_y = body_end_y + V_SP / 1.5
                current_loop_offset_exit = (BASE_HO / 3) + (nesting_level * NEST_OS * loop_offset_factor)
                EXIT_OFFSET_X = current_loop_offset_exit + NEST_OS * 2
                final_join_y = join_exit_y + V_SP / 2
                exit_points = [
                    (start_exit_x, start_exit_y),
                    (start_exit_x + EXIT_OFFSET_X, start_exit_y),
                    (start_exit_x + EXIT_OFFSET_X, final_join_y),
                    (x_center, final_join_y),
                    (x_center, final_join_y + V_SP),
                ]
                draw_multi_point_arrow(canvas, exit_points, text="False", draw_arrow_head=True)

                current_y = final_join_y
                last_connector_x = x_center
                last_connector_y = current_y
                skip_next_connecting_arrow = True
                i = loop_end_index + 1  # Перестрибуємо в кінець
                continue

            # --- 7.5. Блок "IF" / "ELSE IF" ---
            elif line.startswith("Якщо:") or line.startswith("Інакше Якщо:"):
                text = line.replace("Якщо: ", "").replace("Інакше Якщо: ", "").replace(" то", "")

                rhombus_coords = draw_rhombus(canvas, x_center, block_top_y, text, h_scale, v_scale, color_rhombus)

                group_tag = f"rhombus_{BLOCK_ID_COUNTER}"
                BLOCK_TEXT_MAP[group_tag] = text
                BLOCK_ID_COUNTER += 1

                # Знаходимо гілки "True", "False" та кінець блоку
                true_code, false_code, if_end_index = find_if_branches(code_list, i)

                # Розрахунок X-координат для гілок (з урахуванням вкладеності)
                current_branch_offset = (BASE_HO * if_offset_factor) - (
                        NEST_OS * nesting_level * h_scale)
                true_x = x_center + current_branch_offset
                false_x = x_center - (
                        current_branch_offset + (
                        NEST_OS * h_scale * 1.5 * if_offset_factor))

                branch_start_y = rhombus_coords["bottom"][1] + BRANCH_VS

                # Малювання гілки "True"
                p1_true = rhombus_coords["right"]
                p2_true = (true_x, p1_true[1])
                p3_true = (true_x, branch_start_y)
                draw_multi_point_arrow(canvas, [p1_true, p2_true, p3_true], text="True", draw_arrow_head=True)
                (true_end_y, true_end_x) = _draw_flowchart_recursive(canvas, true_code, branch_start_y, true_x,
                                                                     h_scale, v_scale, loop_offset_factor,
                                                                     if_offset_factor, colors, skip_init,
                                                                     nesting_level + 1)

                # Малювання гілки "False" (якщо вона є)
                p1_false = rhombus_coords["left"]
                p2_false = (false_x, p1_false[1])
                join_y = 0  # Y-координата, де гілки з'єднуються

                if false_code:
                    # Випадок: if ... else ...
                    p3_false = (false_x, branch_start_y)
                    draw_multi_point_arrow(canvas, [p1_false, p2_false, p3_false], text="False", draw_arrow_head=True)
                    (false_end_y, false_end_x) = _draw_flowchart_recursive(canvas, false_code, branch_start_y, false_x,
                                                                           h_scale, v_scale, loop_offset_factor,
                                                                           if_offset_factor, colors, skip_init,
                                                                           nesting_level + 1)

                    # Точка з'єднання - нижче обох гілок
                    join_y = max(true_end_y, false_end_y) + V_SP

                    # Малюємо з'єднувальні лінії
                    draw_arrow(canvas, true_end_x, true_end_y, true_end_x, join_y, draw_arrow_head=False)
                    draw_arrow(canvas, true_end_x, join_y, x_center, join_y, draw_arrow_head=False)
                    draw_arrow(canvas, false_end_x, false_end_y, false_end_x, join_y, draw_arrow_head=False)
                    draw_arrow(canvas, false_end_x, join_y, x_center, join_y, draw_arrow_head=False)
                else:
                    # Випадок: if ... (без else)
                    join_y = true_end_y + V_SP

                    # З'єднуємо гілку "True"
                    draw_arrow(canvas, true_end_x, true_end_y, true_end_x, join_y, draw_arrow_head=False)
                    draw_arrow(canvas, true_end_x, join_y, x_center, join_y, draw_arrow_head=False)

                    # Гілка "False" просто огинає блок
                    p3_false = (false_x, p1_false[1] + BRANCH_VS)
                    p4_false = (false_x, join_y)
                    p5_false = (x_center, join_y)
                    draw_multi_point_arrow(canvas, [p1_false, p2_false, p3_false, p4_false, p5_false],
                                           text="False",
                                           draw_arrow_head=False)

                current_y = join_y
                last_connector_x = x_center
                last_connector_y = join_y
                i = if_end_index + 1  # Перестрибуємо в кінець "Все якщо"
                continue

            # --- 7.6. Стандартні (прості) блоки ---
            else:
                y_bottom = 0
                text_to_save = line
                style_key_prefix = "rect"  # Тип блоку за замовчуванням

                if line.startswith("Початок") or line.startswith("Кінець"):
                    style_key_prefix = "ell"
                    is_main = (line == "Початок" or line == "Кінець")
                    if is_main:
                        _, y_bottom = draw_ellipse(canvas, x_center, block_top_y, line, h_scale, v_scale, color_ellipse)
                    else:
                        # (Для функцій)
                        _, y_bottom = draw_subroutine(canvas, x_center, block_top_y, line, h_scale, v_scale, color_sub)
                    text_to_save = line

                elif line.startswith("Виклик:"):
                    style_key_prefix = "sub"
                    text_display = line.replace("Виклик: ", "")
                    _, y_bottom = draw_subroutine(canvas, x_center, block_top_y, text_display, h_scale, v_scale,
                                                  color_sub)
                    text_to_save = text_display

                elif line.startswith("Ввід:"):
                    style_key_prefix = "para"
                    _, y_bottom = draw_parallelogram(canvas, x_center, block_top_y, line, h_scale, v_scale, color_sub)
                    text_to_save = line

                elif line.startswith("Вивід:"):
                    style_key_prefix = "para"
                    _, y_bottom = draw_parallelogram(canvas, x_center, block_top_y, line, h_scale, v_scale, color_sub)
                    text_to_save = line

                else:
                    # Усі інші операції (присвоєння тощо)
                    style_key_prefix = "rect"
                    _, y_bottom = draw_rectangle(canvas, x_center, block_top_y, line, h_scale, v_scale, color_rect)
                    text_to_save = line

                # Збереження тексту для експорту (використовуючи координати як ключ)
                group_tag = f"{style_key_prefix}_{int(x_center)}_{int(block_top_y)}"
                BLOCK_TEXT_MAP[group_tag] = text_to_save

                # Оновлення координат для наступного блоку
                last_connector_y = y_bottom
                last_connector_x = x_center
                current_y = y_bottom
                i += 1

        except Exception as e:
            # Обробка помилок (наприклад, нескінченний цикл у коді)
            # print(f"❌ Помилка в _draw_flowchart_recursive на токені {i}: {line}. Деталі: {e}")
            i += 1

    # Повертаємо координати для виходу з рекурсії
    return (last_connector_y, last_connector_x)


# --- 7. ОСНОВНИЙ ПАРСЕР: C-КОД -> ПСЕВДОКОД ---

def parse_token_list(input_tokens, depth=0):
    """
    Рекурсивно обробляє список токенів C-коду і повертає список
    рядків псевдокоду (з відступами).
    """
    processed_output = []  # Список рядків псевдокоду
    x = 0  # Поточний індекс токена
    n = len(input_tokens)
    indent = "\t" * depth  # Відступ для поточного рівня вкладеності

    while x < n:
        last_x = x  # Для виявлення нескінченних циклів парсера
        try:
            current_word = input_tokens[x]

            # 1. Визначення типу поточного оператора
            is_output_type = current_word == "printf"
            is_input_type = current_word == "scanf"
            is_declaration_type = current_word in ["int", "float", "double", "str", "char", "long"]

            # --- 2. ОБРОБКА КЕРУЮЧИХ КОНСТРУКЦІЙ (FOR, IF, WHILE, DO) ---
            if current_word in ["for", "if", "while", "do"]:

                # --- 2.1. FOR, IF, WHILE ---
                if current_word in ["for", "if", "while"]:
                    # Визначення префіксу псевдокоду
                    prefix = ""
                    if current_word == "for":
                        prefix = "Повторити для"
                    elif current_word == "if":
                        prefix = "Якщо"
                    elif current_word == "while":
                        prefix = "Повторити поки"

                    # Знаходимо умову в дужках (...)
                    open_paren_index = input_tokens.index('(', x + 1)

                    # Пошук *відповідної* закриваючої дужки ')'
                    close_paren_index = -1
                    balance = 0
                    for i in range(open_paren_index, n):
                        if input_tokens[i] == '(':
                            balance += 1
                        elif input_tokens[i] == ')':
                            balance -= 1
                        if balance == 0:
                            close_paren_index = i
                            break
                    if close_paren_index == -1:
                        raise ValueError(f"Невідповідність дужок у {current_word}")

                    # Формуємо заголовок (напр., "i = 0; i < 10; i++")
                    header_part = " ".join(input_tokens[open_paren_index + 1: close_paren_index]).strip()

                    if current_word == "if":
                        processed_output.append(f"{indent}{prefix}: {header_part} то")
                    else:
                        processed_output.append(f"{indent}{prefix}: {header_part}")

                    # Обробка тіла конструкції
                    if close_paren_index + 1 < n and input_tokens[close_paren_index + 1] == '{':
                        # Випадок 1: Тіло у фігурних дужках { ... }
                        code_tokens, end_index = get_block_tokens(input_tokens, close_paren_index + 1)
                        if end_index == -1: raise ValueError(f"Mismatched braces inside {current_word}")

                        # Рекурсивний виклик для тіла
                        nested_processed = parse_token_list(code_tokens, depth + 1)
                        processed_output.extend(nested_processed)
                        x = end_index + 1
                    else:
                        # Випадок 2: Один оператор без дужок (до ';')
                        semicolon_index = input_tokens.index(';', close_paren_index + 1)
                        single_statement_tokens = input_tokens[close_paren_index + 1: semicolon_index + 1]

                        nested_processed = parse_token_list(single_statement_tokens, depth + 1)
                        processed_output.extend(nested_processed)
                        x = semicolon_index + 1

                    # Додаємо маркери кінця блоку
                    if current_word in ["for", "while"]:
                        processed_output.append(f"{indent}Все повторити")

                    # --- 2.2. ОБРОБКА "ELSE" ТА "ELSE IF" ---
                    if current_word == "if":
                        while x < n and input_tokens[x] == "else":
                            if x + 1 < n and input_tokens[x + 1] == "if":
                                # Це "ELSE IF"
                                x += 1  # (Пропускаємо 'else')
                                try:
                                    open_paren_index = input_tokens.index('(', x + 1)
                                    close_paren_index = input_tokens.index(')', open_paren_index)
                                    header_part = " ".join(
                                        input_tokens[open_paren_index + 1: close_paren_index]).strip()
                                except ValueError:
                                    raise ValueError("Malformed 'else if' statement")

                                processed_output.append(f"{indent}Інакше Якщо: {header_part} то")

                                # Обробка тіла 'else if' (з { } або без)
                                if close_paren_index + 1 < n and input_tokens[close_paren_index + 1] == '{':
                                    code_tokens, end_index = get_block_tokens(input_tokens, close_paren_index + 1)
                                    if end_index == -1: raise ValueError("Mismatched braces in 'else if' block")
                                    nested_processed = parse_token_list(code_tokens, depth + 1)
                                    processed_output.extend(nested_processed)
                                    x = end_index + 1
                                else:
                                    semicolon_index = input_tokens.index(';', close_paren_index + 1)
                                    single_statement_tokens = input_tokens[close_paren_index + 1: semicolon_index + 1]
                                    nested_processed = parse_token_list(single_statement_tokens, depth + 1)
                                    processed_output.extend(nested_processed)
                                    x = semicolon_index + 1
                            else:
                                # Це "ELSE"
                                processed_output.append(f"{indent}Інакше")
                                x += 1  # (Пропускаємо 'else')

                                # Обробка тіла 'else' (з { } або без)
                                if x < n and input_tokens[x] == '{':
                                    code_tokens, end_index = get_block_tokens(input_tokens, x)
                                    if end_index == -1: raise ValueError("Mismatched braces in 'else' block")
                                    nested_processed = parse_token_list(code_tokens, depth + 1)
                                    processed_output.extend(nested_processed)
                                    x = end_index + 1
                                else:
                                    semicolon_index = input_tokens.index(';', x)
                                    single_statement_tokens = input_tokens[x: semicolon_index + 1]
                                    nested_processed = parse_token_list(single_statement_tokens, depth + 1)
                                    processed_output.extend(nested_processed)
                                    x = semicolon_index + 1
                                break  # 'else' завжди останній у ланцюжку

                        processed_output.append(f"{indent}Все якщо")  # Маркер кінця 'if/else'

                # --- 2.3. DO-WHILE ---
                elif current_word == "do":
                    if x + 1 < n and input_tokens[x + 1] == '{':
                        # Випадок 1: do { ... } while (...)
                        start_brace_index = x + 1
                        code_tokens, end_brace_index = get_block_tokens(input_tokens, start_brace_index)
                        if end_brace_index == -1: raise ValueError("Mismatched braces in 'do' block")
                        if end_brace_index + 1 >= n or input_tokens[end_brace_index + 1] != 'while':
                            raise ValueError("Expected 'while' after 'do' block")

                        while_index = end_brace_index + 1
                        open_paren_index = while_index + 1
                        close_paren_index = input_tokens.index(')', open_paren_index)
                        header_part = " ".join(input_tokens[open_paren_index + 1: close_paren_index]).strip()

                        processed_output.append(f"{indent}Повторити доки (початок)")
                        nested_processed = parse_token_list(code_tokens, depth + 1)
                        processed_output.extend(nested_processed)
                        processed_output.append(f"{indent}Повторити доки (умова): {header_part}")

                        semicolon_index = input_tokens.index(';', close_paren_index)
                        x = semicolon_index + 1
                    else:
                        # Випадок 2: do ... while (...)
                        semicolon_index = input_tokens.index(';', x + 1)
                        code_tokens = input_tokens[x + 1: semicolon_index + 1]
                        if semicolon_index + 1 >= n or input_tokens[semicolon_index + 1] != 'while':
                            raise ValueError("Expected 'while' after 'do' statement")

                        while_index = semicolon_index + 1
                        open_paren_index = while_index + 1
                        close_paren_index = input_tokens.index(')', open_paren_index)
                        header_part = " ".join(input_tokens[open_paren_index + 1: close_paren_index]).strip()

                        processed_output.append(f"{indent}Повторити доки (початок)")
                        nested_processed = parse_token_list(code_tokens, depth + 1)
                        processed_output.extend(nested_processed)
                        processed_output.append(f"{indent}Повторити доки (умова): {header_part}")

                        semicolon_index_final = input_tokens.index(';', close_paren_index)
                        x = semicolon_index_final + 1
                continue

            # --- 3. ОБРОБКА ІНІЦІАЛІЗАЦІЇ ЗМІННИХ ---
            elif is_declaration_type:
                semicolon_index = input_tokens.index(';', x)
                # Беремо все між типом (int) та ';'
                declaration_line = " ".join(input_tokens[x + 1: semicolon_index]).strip()
                processed_output.append(f"{indent}Ініціалізація: {declaration_line}")
                x = semicolon_index + 1
                continue

            # --- 4. ОБРОБКА ВВОДУ/ВИВОДУ (printf/scanf) ---
            elif is_output_type or is_input_type:
                try:
                    semicolon_index = input_tokens.index(';', x)
                    io_statement = " ".join(input_tokens[x: semicolon_index + 1]).strip()
                    prefix = "Вивід" if is_output_type else "Ввід"

                    # Спрощена логіка: шукаємо змінну після першої коми
                    match_var = re.search(r',\s*(.*)\s*\)', io_statement)
                    if match_var:
                        variable = match_var.group(1)
                        processed_output.append(f"{indent}{prefix}: {variable}")
                    else:
                        if not is_output_type:  # (scanf)
                            processed_output.append(f"{indent}Ввід: Невідома змінна")
                        # (printf без змінних, напр. "Hello", ігноруємо)

                    x = semicolon_index + 1
                    continue
                except ValueError:
                    # (Якщо в рядку немає ';', пропускаємо)
                    try:
                        x += input_tokens[x:].index(';') + 1
                    except ValueError:
                        x += 1
                    continue

            # --- 5. ПРОПУСК (fflush) ---
            elif current_word == "fflush":
                semicolon_index = input_tokens.index(';', x)
                x = semicolon_index + 1
                continue

            # --- 6. ОБРОБКА ВИКЛИКУ ФУНКЦІЇ ---
            elif x + 1 < n and input_tokens[x + 1] == '(':
                # (Якщо наступний токен - дужка, це виклик функції)
                open_paren_index = x + 1
                close_paren_index = input_tokens.index(')', open_paren_index)
                semicolon_index = input_tokens.index(';', close_paren_index)
                values = " ".join(input_tokens[open_paren_index + 1: close_paren_index]).strip()
                processed_output.append(f"{indent}Виклик: {current_word}({values})")
                x = semicolon_index + 1
                continue

            # --- 7. ОБРОБКА "RETURN" ---
            elif current_word == "return":
                semicolon_index = input_tokens.index(';', x)
                tokens_in_statement = input_tokens[x:semicolon_index]
                statement_line = " ".join(tokens_in_statement).strip()

                # Малюємо блок 'return' тільки якщо він щось повертає
                # (Ігноруємо 'return;' та 'return 0;')
                if statement_line and statement_line != "return 0":
                    processed_output.append(f"{indent}{statement_line}")

                x = semicolon_index + 1
                continue

            # --- 8. ОБРОБКА ІНШИХ ОПЕРАТОРІВ (ПРИСВОЄННЯ) ---
            else:
                semicolon_index = input_tokens.index(';', x)
                statement_line = " ".join(input_tokens[x: semicolon_index]).strip()
                if statement_line:  # (Якщо рядок не порожній)
                    processed_output.append(f"{indent}{statement_line}")
                x = semicolon_index + 1
                continue

        except ValueError:
            # (Якщо сталася помилка парсингу, напр. 'index' не знайшов ';')
            x += 1

        # Захист від нескінченного циклу парсера
        if x == last_x and x < n:
            print(f"Infinite loop detected! Force skipping token: {input_tokens[x]}")
            x += 1

    return processed_output


def find_function_bodies(tokens):
    """
    Знаходить усі функції у списку токенів та вилучає їхні тіла та аргументи.

    Повертає: {func_name: {"args": [tokens], "body": [tokens]}}
    """
    function_map = {}
    i = 0
    n = len(tokens)

    while i < n:
        # 1. Шукаємо потенційний початок функції (тип повернення)
        if tokens[i] in ["int", "void", "float", "double", "char", "long"]:
            function_name_index = i + 1
            if function_name_index < n:
                function_name = tokens[function_name_index]

                # 2. Перевіряємо, чи є '(', що вказує на функцію
                if function_name_index + 1 < n and tokens[function_name_index + 1] == '(':
                    open_paren_index = function_name_index + 1

                    # 3. Шукаємо відповідну ')' для аргументів
                    close_paren_index = -1
                    paren_balance = 0
                    k = open_paren_index
                    while k < n:
                        if tokens[k] == '(':
                            paren_balance += 1
                        elif tokens[k] == ')':
                            paren_balance -= 1

                        if paren_balance == 0:
                            close_paren_index = k
                            break

                        if tokens[k] == ';':  # Це прототип (оголошення), а не тіло
                            close_paren_index = -2
                            break
                        k += 1

                    if close_paren_index <= 0:
                        i += 1  # Помилка або прототип, пропускаємо
                        continue

                    # 4. Зберігаємо список токенів-аргументів
                    arg_tokens = tokens[open_paren_index + 1: close_paren_index]

                    # 5. Шукаємо '{' (початок тіла функції)
                    start_brace_index = -1
                    j = close_paren_index + 1  # Пошук *після* ')'
                    while j < n:
                        if tokens[j] == '{':
                            start_brace_index = j
                            break
                        if tokens[j] == ';':  # Це був прототип
                            start_brace_index = -2
                            break
                        j += 1

                    if start_brace_index == -2:  # Прототип
                        i = j + 1
                        continue

                    # 6. Вилучаємо тіло функції
                    if start_brace_index != -1:
                        body_tokens, end_brace_index = get_block_tokens(tokens, start_brace_index)

                        function_map[function_name] = {
                            "args": arg_tokens,
                            "body": body_tokens
                        }

                        i = end_brace_index + 1  # Перестрибуємо в кінець функції
                        continue
        i += 1

    # Резервний варіант (якщо код - це лише 'main' без 'int main()')
    if not function_map and tokens:
        function_map["main"] = {"args": [], "body": tokens}
    return function_map


def tokenize_code(code_string):
    """
    (Не використовується, логіка дублюється в select_file_...)
    Розбиває C-код на токени, зберігаючи оператори, дужки та роздільники.
    """
    # Додаємо пробіли навколо складних операторів
    code_string = code_string.replace('!=', ' != ')
    code_string = code_string.replace('==', ' == ')
    code_string = code_string.replace('<=', ' <= ')
    code_string = code_string.replace('>=', ' >= ')
    code_string = code_string.replace('++', ' ++ ')
    code_string = code_string.replace('--', ' -- ')
    code_string = code_string.replace('+=', ' += ')
    code_string = code_string.replace('-=', ' -= ')
    code_string = code_string.replace('||', ' || ')
    code_string = code_string.replace('&&', ' && ')

    # Додаємо пробіли навколо простих символів
    symbols = ['(', ')', '{', '}', ';', ',', '=', '+', '-', '*', '/', '>', '<', '!']
    for sym in symbols:
        code_string = code_string.replace(sym, f' {sym} ')

    tokens = code_string.split()

    # Фільтрація коментарів (/* ... */) та директив (#define, #include)
    filtered_tokens = []
    in_comment = False
    in_define = False
    for token in tokens:
        if token.startswith('/*'):
            in_comment = True
            continue
        if token.endswith('*/'):
            in_comment = False
            continue
        if token.startswith('#'):
            in_define = True
            continue
        if in_define and token.endswith('\\'):  # Багаторядковий #define
            continue
        if in_define and not token.endswith('\\'):
            in_define = False
            continue
        if not in_comment and not in_define:
            filtered_tokens.append(token)
    return filtered_tokens


# =======================================================
# --- 8. ЛОГІКА ПРИВ'ЯЗКИ ТА ОНОВЛЕННЯ СТРІЛОК ---
# =======================================================


def _update_arrow_mapping(arrow_id, source_tag=None, target_tag=None):
    """
    Оновлює глобальні словники ARROW_CONNECTIONS та BLOCK_TO_ARROWS.
    Це "мозок", що керує зв'язками стрілок та блоків.

    source_tag/target_tag = None: Не змінювати.
    source_tag/target_tag = False: Розірвати зв'язок (від'єднати).
    source_tag/target_tag = 'tag': Встановити/змінити зв'язок.
    """
    global ARROW_CONNECTIONS
    global BLOCK_TO_ARROWS

    arrow_id_int = int(arrow_id)

    # 1. Отримуємо поточні (старі) зв'язки для цієї стрілки
    if arrow_id_int not in ARROW_CONNECTIONS:
        ARROW_CONNECTIONS[arrow_id_int] = {'source_tag': None, 'target_tag': None}
    old_source_tag = ARROW_CONNECTIONS[arrow_id_int].get('source_tag')
    old_target_tag = ARROW_CONNECTIONS[arrow_id_int].get('target_tag')

    # 2. Визначаємо нові зв'язки на основі вхідних параметрів
    new_source_tag = old_source_tag
    new_target_tag = old_target_tag

    if source_tag is not None:
        new_source_tag = source_tag if source_tag is not False else None
    if target_tag is not None:
        new_target_tag = target_tag if target_tag is not False else None

    # 3. Оновлюємо головний словник (ARROW_CONNECTIONS)
    ARROW_CONNECTIONS[arrow_id_int]['source_tag'] = new_source_tag
    ARROW_CONNECTIONS[arrow_id_int]['target_tag'] = new_target_tag

    # 4. Оновлюємо зворотний словник (BLOCK_TO_ARROWS)

    # 4.1. Видаляємо старий Source (якщо він змінився або був розірваний)
    if old_source_tag and old_source_tag != new_source_tag and \
            old_source_tag in BLOCK_TO_ARROWS and \
            arrow_id_int in BLOCK_TO_ARROWS[old_source_tag]:

        BLOCK_TO_ARROWS[old_source_tag].remove(arrow_id_int)
        if not BLOCK_TO_ARROWS[old_source_tag]:
            del BLOCK_TO_ARROWS[old_source_tag]

    # 4.2. Видаляємо старий Target (якщо він змінився або був розірваний)
    if old_target_tag and old_target_tag != new_target_tag and \
            old_target_tag in BLOCK_TO_ARROWS and \
            arrow_id_int in BLOCK_TO_ARROWS[old_target_tag]:

        BLOCK_TO_ARROWS[old_target_tag].remove(arrow_id_int)
        if not BLOCK_TO_ARROWS[old_target_tag]:
            del BLOCK_TO_ARROWS[old_target_tag]

    # 5. Додаємо нові зв'язки у зворотний словник
    if new_source_tag:
        if new_source_tag not in BLOCK_TO_ARROWS:
            BLOCK_TO_ARROWS[new_source_tag] = []
        if arrow_id_int not in BLOCK_TO_ARROWS[new_source_tag]:
            BLOCK_TO_ARROWS[new_source_tag].append(arrow_id_int)

    if new_target_tag:
        if new_target_tag not in BLOCK_TO_ARROWS:
            BLOCK_TO_ARROWS[new_target_tag] = []
        if arrow_id_int not in BLOCK_TO_ARROWS[new_target_tag]:
            BLOCK_TO_ARROWS[new_target_tag].append(arrow_id_int)


def _snap_to_closest_block_point(canvas, x, y):
    """
    Шукає найближчий невидимий порт ("block_port") у радіусі 'tolerance'.

    Повертає: (координати_порту, тег_групи_блоку, тип_об'єкта)
    """
    tolerance = PORT_SNAPPING_TOLERANCE

    # Шукаємо об'єкти в квадраті навколо (x, y)
    overlapping = canvas.find_overlapping(
        x - tolerance, y - tolerance,
        x + tolerance, y + tolerance
    )

    closest_port_obj_id = None
    min_distance = float('inf')

    # Шукаємо серед знайдених *тільки* порти
    for obj_id in reversed(overlapping):
        tags = canvas.gettags(obj_id)
        if "block_port" in tags:
            coords = canvas.coords(obj_id)
            if len(coords) != 4: continue

            # Центр порту
            port_x = (coords[0] + coords[2]) / 2
            port_y = (coords[1] + coords[3]) / 2

            distance = ((x - port_x) ** 2 + (y - port_y) ** 2) ** 0.5

            if distance < min_distance:
                min_distance = distance
                closest_port_obj_id = obj_id

    if closest_port_obj_id:
        # Знайшли порт. Тепер треба знайти тег його "батьківського" блоку.
        port_tags = canvas.gettags(closest_port_obj_id)
        group_tag = next(
            (tag for tag in port_tags if tag.startswith(("sub_", "rect_", "rhombus_", "ell_", "para_", "hex_"))),
            None)

        if group_tag:
            # Повертаємо центр порту та тег блоку
            port_coords = canvas.coords(closest_port_obj_id)
            px = (port_coords[0] + port_coords[2]) / 2
            py = (port_coords[1] + port_coords[3]) / 2

            return (px, py), group_tag, "port"

    # Якщо нічого не знайдено, повертаємо вихідні координати
    return (x, y), None, None


def _auto_snap_all_arrows(canvas):
    """
    Викликається після першого малювання.
    Пробігає по всіх стрілках і автоматично "приклеює" їхні кінці
    до найближчих портів блоків.
    """
    all_arrows = canvas.find_withtag("flow_arrow")

    for arrow_id_str in all_arrows:
        try:
            arrow_id = int(arrow_id_str)
        except ValueError:
            continue

        coords = list(canvas.coords(arrow_id))
        if len(coords) < 4: continue

        x_start, y_start = coords[0], coords[1]
        x_end, y_end = coords[-2], coords[-1]

        new_coords = list(coords)
        source_tag = None
        target_tag = None

        # 1. Прив'язка початку (Source)
        snap_point_s, block_tag_s, _ = _snap_to_closest_block_point(canvas, x_start, y_start)
        if block_tag_s:
            new_coords[0], new_coords[1] = snap_point_s
            source_tag = block_tag_s

        # 2. Прив'язка кінця (Target)
        snap_point_t, block_tag_t, _ = _snap_to_closest_block_point(canvas, x_end, y_end)
        if block_tag_t:
            new_coords[-2], new_coords[-1] = snap_point_t
            target_tag = block_tag_t

        # 3. Оновлення координат стрілки на полотні
        if new_coords != coords:
            canvas.coords(arrow_id, *new_coords)

        # 4. Оновлення логіки зв'язків
        if source_tag or target_tag:
            _update_arrow_mapping(arrow_id, source_tag=source_tag, target_tag=target_tag)


def draw_flowchart_with_offset(canvas, code_list, h_scale, v_scale, loop_offset_factor, if_offset_factor, colors,
                               skip_init, is_grid_visible):
    """
    Головна "обгортка" для малювання.

    1. Очищує полотно та глобальні словники.
    2. Викликає рекурсивне малювання.
    3. Викликає автоматичне "прилипання" стрілок.
    4. Динамічно налаштовує розмір сітки та scrollregion.
    """
    canvas.delete("all")

    # --- КРОК 1: Скидання стану ---
    global ARROW_CONNECTIONS
    global BLOCK_TO_ARROWS
    global BLOCK_TEXT_MAP
    global BLOCK_ID_COUNTER

    ARROW_CONNECTIONS.clear()
    BLOCK_TO_ARROWS.clear()
    BLOCK_TEXT_MAP.clear()
    BLOCK_ID_COUNTER = 0

    # Початковий (великий) розмір полотна
    EXTENDED_SIZE_INITIAL = 2000
    canvas.config(scrollregion=(0, 0, EXTENDED_SIZE_INITIAL, EXTENDED_SIZE_INITIAL))

    # --- КРОК 2: Рекурсивне малювання ---
    _draw_flowchart_recursive(canvas, code_list, Y_START, EXTENDED_SIZE_INITIAL / 2, h_scale, v_scale,
                              loop_offset_factor,
                              if_offset_factor, colors, skip_init, nesting_level=0)

    canvas.update_idletasks()

    # --- КРОК 3: Автоматична прив'язка стрілок ---
    _auto_snap_all_arrows(canvas)

    # --- КРОК 4: Налаштування ScrollRegion та Сітки ---

    # Знаходимо межі *тільки* блоків та стрілок (ігноруючи сітку)
    content_items = canvas.find_withtag("block") + canvas.find_withtag("flow_arrow")
    actual_bbox = canvas.bbox(*content_items) if content_items else None

    if actual_bbox:
        x0, y0, x1, y1 = actual_bbox
        PADDING = 100  # Відступ навколо вмісту
        MIN_SIZE = 800  # Мінімальний розмір полотна

        max_x = max(MIN_SIZE, x1 + PADDING)
        max_y = max(MIN_SIZE, y1 + PADDING)

        # (Обмеження, щоб уникнути проблем з рендерингом Tkinter)
        MAX_CANVAS_LIMIT = 8000
        final_x = min(MAX_CANVAS_LIMIT, max_x)
        final_y = min(MAX_CANVAS_LIMIT, max_y)

        # 4.1. Встановлюємо scrollregion за розміром вмісту + відступи
        canvas.config(scrollregion=(
            x0 - PADDING,
            y0 - PADDING,
            final_x,
            final_y
        ))

        # 4.2. Малюємо сітку, що покриває всю область
        draw_grid_lines(canvas, GRID_SIZE, int(max(final_x, final_y)), is_grid_visible)

    else:
        # Полотно порожнє
        canvas.config(scrollregion=(0, 0, 800, 800))
        draw_grid_lines(canvas, GRID_SIZE, 800, is_grid_visible)


def _update_colors_only(canvas, colors):
    """Швидко оновлює кольори існуючих блоків без перемальовування."""
    (color_ellipse, color_rect, color_rhombus, color_sub, color_hex) = colors

    # Використовуємо теги типів, присвоєні при малюванні
    canvas.itemconfig("ellipse", fill=color_ellipse)
    canvas.itemconfig("rect", fill=color_rect)
    canvas.itemconfig("rhombus", fill=color_rhombus)
    canvas.itemconfig("sub", fill=color_sub)
    canvas.itemconfig("para", fill=color_sub)  # Паралелограми (Ввід/Вивід)
    canvas.itemconfig("hex", fill=color_hex)


# --- 9. ГОЛОВНЕ ВІКНО GUI ТА ОБРОБНИКИ ПОДІЙ ---

def draw_flowchart_window(root, function_map):
    """
    Створює та керує головним вікном редактора блок-схем.
    """
    SNAP_THRESHOLD = 5  # Допуск "прилипання" стрілки до сітки (px)
    ARROW_GRID_SIZE = 25  # Крок сітки для точок стрілок
    # --- Глобальні змінні для цього вікна ---
    grid_visible_var = tk.BooleanVar(value=True)  # Стан чекбоксу "Сітка"
    arrow_data = {"id": None, "coords": [], "points_vis": []}  # Для редагування стрілок

    if "main" not in function_map:
        print("Не можу намалювати: функція 'main' не знайдена.")
        root.destroy()
        return

    # --- 1. Налаштування вікна ---
    draw_window = tk.Toplevel(root)
    draw_window.title("Блок-схема")
    draw_window.geometry("1400x800")
    draw_window.protocol("WM_DELETE_WINDOW", root.destroy)  # Закрити все при виході

    # --- 2. Розмітка GUI (Панелі) ---
    # 2.1. Ліва панель інструментів (кнопки)
    zoom_display_var = tk.StringVar(draw_window, value=f"{GLOBAL_SCALE_FACTOR_X:.2f}x")
    left_toolbar_frame = tk.Frame(draw_window, width=170, bd=1, relief="raised");
    left_toolbar_frame.pack(side=tk.LEFT, fill=tk.Y, padx=5, pady=5);
    left_toolbar_frame.pack_propagate(False)  # Фіксована ширина

    # 2.2. Основний вміст (панелі керування + полотно)
    main_content_frame = tk.Frame(draw_window);
    main_content_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)

    # 2.3. Верхня панель (повзунки)
    control_frame = tk.Frame(main_content_frame);
    control_frame.pack(side=tk.TOP, fill=tk.X, pady=5)

    # 2.4. Панель кольорів
    color_frame = tk.Frame(main_content_frame);
    color_frame.pack(side=tk.TOP, fill=tk.X, pady=(0, 5))

    # 2.5. Фрейм для полотна та скролбарів
    canvas_frame = tk.Frame(main_content_frame);
    canvas_frame.pack(fill=tk.BOTH, expand=1)

    # --- 3. Глобальні змінні Tkinter (Controls) ---
    function_names = list(function_map.keys());
    selected_func = tk.StringVar(draw_window);
    selected_func.set("main");
    show_minimap_var = tk.BooleanVar(value=True)

    # 3.1. Скролбари та Полотно (Canvas)
    v_scroll = tk.Scrollbar(canvas_frame, orient=tk.VERTICAL);
    v_scroll.pack(side=tk.RIGHT, fill=tk.Y)
    h_scroll = tk.Scrollbar(canvas_frame, orient=tk.HORIZONTAL);
    h_scroll.pack(side=tk.BOTTOM, fill=tk.X)
    canvas = tk.Canvas(canvas_frame, bg="white", yscrollcommand=v_scroll.set, xscrollcommand=h_scroll.set);
    canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=1)

    # 3.2. Налаштування міні-карти
    MINIMAP_W, MINIMAP_H = 200, 160
    _scroll_debounce_job = None;  # Для затримки оновлення міні-карти
    SCROLL_DEBOUNCE_MS = 150

    # 3.3. Змінні для повзунків
    h_scale_var = tk.DoubleVar(value=1.0);
    v_scale_var = tk.DoubleVar(value=1.0);
    loop_offset_var = tk.DoubleVar(value=1.0);
    if_offset_var = tk.DoubleVar(value=1.0)

    # 3.4. Змінні для кольорів
    ellipse_color_var = tk.StringVar(value="#FFD1DC");
    rect_color_var = tk.StringVar(value="#ADD8E6");
    rhombus_color_var = tk.StringVar(value="#FFFFE0");
    sub_color_var = tk.StringVar(value="#CCEEFF");
    hex_color_var = tk.StringVar(value="#D8BFD8");

    # 3.5. Змінні для чекбоксів
    skip_init_var = tk.BooleanVar(value=False)

    # --- 4. ДОПОМІЖНІ ФУНКЦІЇ (ЗАМИКАННЯ GUI) ---
    # (Ці функції мають доступ до 'canvas', 'h_scale_var' тощо)

    def _update_minimap_viewport(*args):
        """Оновлює червоний прямокутник на міні-карті."""
        if not show_minimap_var.get(): return
        try:
            bbox = canvas.bbox("all")  # Межі всього вмісту
            if not bbox: return

            x0_total, y0_total, x1_total, y1_total = bbox
            w_total, h_total = x1_total - x0_total, y1_total - y0_total
            if w_total == 0 or h_total == 0: return

            # Розміри віджету міні-карти
            minimap_w = minimap_canvas.winfo_width();
            minimap_h = minimap_canvas.winfo_height()
            if minimap_w <= 1 or minimap_h <= 1: return

            # Коефіцієнти масштабування
            scale_x = minimap_w / w_total;
            scale_y = minimap_h / h_total

            # Поточна видима область (0.0 ... 1.0)
            x_view = canvas.xview();
            y_view = canvas.yview()

            # Розрахунок координат червоного прямокутника
            r_x0 = (x_view[0] * w_total) * scale_x;
            r_y0 = (y_view[0] * h_total) * scale_y
            r_x1 = (x_view[1] * w_total) * scale_x;
            r_y1 = (y_view[1] * h_total) * scale_y

            minimap_canvas.coords("viewport", r_x0, r_y0, r_x1, r_y1);
            minimap_canvas.tag_raise("viewport")
        except Exception:
            pass  # (Помилки можуть виникати при зміні розміру вікна)

    def _toggle_minimap():
        """Ховає або показує міні-карту."""
        if show_minimap_var.get():
            minimap_frame.place(relx=1.0, rely=1.0, anchor="se", x=-10, y=-10)
            _update_minimap_viewport()
        else:
            minimap_frame.place_forget()

    def _update_minimap_viewport_debounced():
        """Оновлення міні-карти з затримкою (debounce) під час скролу."""
        if not show_minimap_var.get(): return
        nonlocal _scroll_debounce_job
        if _scroll_debounce_job: draw_window.after_cancel(_scroll_debounce_job)
        _scroll_debounce_job = draw_window.after(SCROLL_DEBOUNCE_MS, _update_minimap_viewport)

    def _on_minimap_click(event):
        """Переміщує основне полотно при кліку на міні-карту."""
        if not show_minimap_var.get(): return
        try:
            bbox = canvas.bbox("all")
            if not bbox: return

            x0_total, y0_total, x1_total, y1_total = bbox;
            w_total, h_total = x1_total - x0_total, y1_total - y0_total
            if w_total == 0 or h_total == 0: return

            minimap_w = minimap_canvas.winfo_width();
            minimap_h = minimap_canvas.winfo_height()

            # Відсоток кліку на міні-карті
            click_perc_x = event.x / minimap_w;
            click_perc_y = event.y / minimap_h

            # Розмір видимої області (в %)
            viewport_frac_x = canvas.xview()[1] - canvas.xview()[0];
            viewport_frac_y = canvas.yview()[1] - canvas.yview()[0]

            # Центруємо нову область навколо кліку
            new_x_start = click_perc_x - (viewport_frac_x / 2);
            new_y_start = click_perc_y - (viewport_frac_y / 2)

            # Обмеження (щоб не вийти за 0.0 - 1.0)
            new_x_start = max(0.0, min(new_x_start, 1.0 - viewport_frac_x));
            new_y_start = max(0.0, min(new_y_start, 1.0 - viewport_frac_y))

            canvas.xview_moveto(new_x_start);
            canvas.yview_moveto(new_y_start);
            _update_minimap_viewport()  # Миттєве оновлення
        except Exception as e:
            print(f"Помилка кліку по міні-карті: {e}")

    # --- 4.1. Обробники навігації (Pan/Zoom/Scroll) ---

    def _on_pan_start(event):
        """Початок панорамування (середня кнопка миші або Shift+ЛКМ)."""
        canvas.config(cursor="fleur");
        canvas.scan_mark(event.x, event.y)

    def _on_pan_move(event):
        """Рух полотна під час панорамування."""
        canvas.scan_dragto(event.x, event.y, gain=1)

    def _on_pan_end(event):
        """Завершення панорамування."""
        canvas.config(cursor="");
        _update_minimap_viewport()  # Оновлюємо карту після руху

    def _on_mouse_wheel(event):
        """
        Масштабування (Zoom) вмісту Canvas (Ctrl + Колесо миші) з фіксованим кроком.
        """
        global GLOBAL_SCALE_FACTOR_X
        global GLOBAL_SCALE_FACTOR_Y

        # Перевіряємо, чи натиснуто Ctrl
        if event.state & 4:

            # Визначаємо, чи Zoom In чи Zoom Out
            if event.delta > 0 or event.num == 4:
                # Zoom In (Збільшення: множимо на 10.0)
                scale_change = ZOOM_STEP_MULTIPLIER
            else:
                # Zoom Out (Зменшення: ділимо на 10.0, але не нижче 0.1)
                scale_change = 1.0 / ZOOM_STEP_MULTIPLIER

            # 1. Скидаємо Canvas до 1.0 перед перерахунком (це для коректного збереження ручного стану)
            # Щоб застосувати чистий множник, ми повинні знати поточний стан Zoom
            scale_reset_x = 1.0 / GLOBAL_SCALE_FACTOR_X
            scale_reset_y = 1.0 / GLOBAL_SCALE_FACTOR_Y
            canvas.scale("all", 0, 0, scale_reset_x, scale_reset_y)

            # 2. Оновлюємо глобальний фактор
            GLOBAL_SCALE_FACTOR_X *= scale_change
            GLOBAL_SCALE_FACTOR_Y *= scale_change

            # Обмеження мінімального масштабу (наприклад, до 0.1x)
            GLOBAL_SCALE_FACTOR_X = max(0.1, GLOBAL_SCALE_FACTOR_X)
            GLOBAL_SCALE_FACTOR_Y = max(0.1, GLOBAL_SCALE_FACTOR_Y)

            # 3. Повторно застосовуємо новий візуальний Zoom
            canvas.scale("all", 0, 0, GLOBAL_SCALE_FACTOR_X, GLOBAL_SCALE_FACTOR_Y)

            # 4. Обчислюємо новий розмір шрифту та оновлюємо GUI
            new_h = GLOBAL_SCALE_FACTOR_X
                # Синхронізація шкали Zoom
            zoom_display_var.set(f"{new_h:.2f}x")

            # Оновлюємо ScrollRegion
            canvas.configure(scrollregion=canvas.bbox("all"))
            _update_minimap_viewport()

            return

        # Якщо Ctrl не натиснуто, виконуємо звичайний скрол
        _on_vertical_scroll(event)

    def _on_vertical_scroll(event):
        """Вертикальна прокрутка (Колесо миші)."""
        if event.num == 5 or event.delta < 0:
            delta = 1
        elif event.num == 4 or event.delta > 0:
            delta = -1
        else:
            return

        canvas.yview_scroll(delta, "units");
        _update_minimap_viewport_debounced()  # Оновлення з затримкою

    def _on_horizontal_scroll(event):
        """Горизонтальна прокрутка (Shift + Колесо миші)."""
        if event.num == 5 or event.delta < 0:
            delta = 1
        elif event.num == 4 or event.delta > 0:
            delta = -1
        else:
            return

        canvas.xview_scroll(delta, "units");
        _update_minimap_viewport_debounced()

    # --- 4.2. Обробники кнопок ---

    def open_help_window():
        """Вікно з довідкою."""
        help_window = tk.Toplevel(draw_window);
        help_window.title("Допомога та Інформація");
        help_window.geometry("500x400");
        help_window.transient(draw_window)
        text_frame = tk.Frame(help_window);
        text_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        scrollbar = tk.Scrollbar(text_frame);
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        help_text_widget = tk.Text(text_frame, wrap=tk.WORD, yscrollcommand=scrollbar.set, font=("Arial", 10));
        help_text_widget.pack(fill=tk.BOTH, expand=True);
        scrollbar.config(command=help_text_widget.yview)
        help_text = """
    === Довідка по Flowchart Generator ===

    1. Навігація:
       - **Прокрутка:** Колесо миші (вертикально) або Shift + Колесо (горизонтально).
       - **Панорамування:** Затисніть середню кнопку миші (колесо) або Shift + ЛКМ та рухайте мишу.
       - **Масштаб:** Затисніть Ctrl та крутіть колесо миші.
       - **Міні-карта:** Клікніть по міні-карті для швидкого переходу.

    2. Редагування:
       - **Рух блоків:** Натисніть ЛКМ на блок та перетягніть. Блок автоматично "прилипне" до сітки.
       - **Рух стрілок:** Натисніть ЛКМ біля кінця стрілки, щоб побачити точки.
       - **Переміщення точок:** Перетягніть червону точку.
       - **Прив'язка стрілки:** Перетягніть кінцеву точку до блоку, поки вона не "прилипне".
       - **Від'єднання стрілки:** Перетягніть кінцеву точку від блоку.
       - **Вирівнювання стрілок:** Проміжні точки стрілки "прилипають" до сітки та до ортогональних (90°) ліній.

    3. Експорт:
       - **Повна БС (.png):** Зберігає всю діаграму, навіть ту, що не видно на екрані (рекомендовано).
       - **Видима БС (.png):** Робить скріншот видимої частини вікна.
       - **Експорт в .drawio:** Зберігає у форматі, сумісному з diagrams.net (Draw.io).
       - **Псевдокод (.txt):** Зберігає псевдокод поточної функції.
    """
        help_text_widget.insert(tk.END, help_text);
        help_text_widget.config(state=tk.DISABLED)

    def save_pseudocode():
        """Зберігає згенерований псевдокод у .txt файл."""
        selected_name = selected_func.get();
        code_list = function_map.get(selected_name, [])
        if not code_list: print("Немає псевдокоду для експорту."); return

        txt_path = filedialog.asksaveasfilename(title="Зберегти псевдокоду як .txt",
                                                initialfile=f"pseudocode_{selected_func.get()}.txt",
                                                defaultextension=".txt",
                                                filetypes=(("Text files", "*.txt"), ("All files", "*.*")))
        if not txt_path: return
        try:
            text_content = "\n".join(code_list)
            with open(txt_path, 'w', encoding='utf-8') as f:
                f.write(text_content)
            print(f"✅ Псевдокоду успішно збережено у: {txt_path}")
        except Exception as e:
            print(f"❌ Помилка при збереженні псевдокоду: {e}")

    def save_full_diagram_ps():
        """Збереження повної діаграми (через PostScript)."""
        png_path = filedialog.asksaveasfilename(title="Зберегти повну діаграму як PNG (надійний експорт)",
                                                initialfile=f"flowchart_{selected_func.get()}_full.png",
                                                defaultextension=".png",
                                                filetypes=(("PNG files", "*.png"), ("All files", "*.*")))
        if png_path: save_full_flowchart_as_png_via_pil(canvas, png_path)

    def save_visible_diagram_png():
        """Збереження видимої частини (скріншот)."""
        png_path = filedialog.asksaveasfilename(title="Зберегти видиму діаграму як PNG...",
                                                initialfile=f"flowchart_{selected_func.get()}.png",
                                                defaultextension=".png",
                                                filetypes=(("PNG files", "*.png"), ("All files", "*.*")))
        if png_path: save_canvas_screenshot(canvas, png_path)

    def save_as_drawio():
        """Експорт у формат .drawio (XML)."""
        selected_name = selected_func.get();
        xml_path = filedialog.asksaveasfilename(title="Зберегти як .drawio XML",
                                                initialfile=f"flowchart_{selected_func.get()}.drawio",
                                                defaultextension=".drawio",
                                                filetypes=(("Draw.io files", "*.drawio"), ("XML files", "*.xml"),
                                                           ("All files", "*.*")))
        if not xml_path: return
        try:
            xml_content = generate_drawio_xml_from_canvas(canvas, selected_name)
            with open(xml_path, 'w', encoding='utf-8') as f:
                f.write(xml_content)
            print(f"✅ Діаграма успішно збережена у .drawio: {xml_path}")
        except Exception as e:
            print(f"❌ Помилка при експорті .drawio: {e}");
            import traceback;
            traceback.print_exc()

    # --- 4.3. Головна функція оновлення ---

    def update_drawing(*args):
        """
        Повністю перемальовує полотно та ПОВТОРНО ЗАСТОСОВУЄ ТЕКСТОВИЙ ТА ВІЗУАЛЬНИЙ ЗУМ.
        """
        global GLOBAL_SCALE_FACTOR_X
        global GLOBAL_SCALE_FACTOR_Y
        # 1. Швидке оновлення кольорів
        if len(args) == 3 and isinstance(args[0], str) and ('color_var' in args[0]):
            try:
                colors = (ellipse_color_var.get(), rect_color_var.get(), rhombus_color_var.get(),
                          sub_color_var.get(), hex_color_var.get())
                _update_colors_only(canvas, colors)
                return
            except tk.TclError:
                return
        # 2. Повне перемалювання
        try:
            zoom_display_var.set(f"{GLOBAL_SCALE_FACTOR_X:.2f}x")
            is_grid_visible = grid_visible_var.get()
            h_scale = h_scale_var.get();
            v_scale = v_scale_var.get();
            loop_offset_factor = loop_offset_var.get();
            if_offset_factor = if_offset_var.get();
            selected_name = selected_func.get();
            skip_init = skip_init_var.get()
            colors = (ellipse_color_var.get(), rect_color_var.get(), rhombus_color_var.get(), sub_color_var.get(),
                      hex_color_var.get())
        except tk.TclError:
            return
        print(
            f"Оновлення: Функція='{selected_name}', Масштаб (ШxВ): {h_scale:.2f}x{v_scale:.2f}, ... [ПОВНЕ ПЕРЕМАЛЬОВУВАННЯ]")
        code_list = function_map.get(selected_name, [])
        # 3. Виклик головної функції малювання
        draw_flowchart_with_offset(canvas, code_list, h_scale, v_scale, loop_offset_factor, if_offset_factor, colors,
                                   skip_init, is_grid_visible)
        # 4. ПОВТОРНЕ ЗАСТОСУВАННЯ ВІЗУАЛЬНОГО ЗУМУ
        # Використовуємо Canvas.scale для повторного застосування Zoom
        if GLOBAL_SCALE_FACTOR_X != 1.0 or GLOBAL_SCALE_FACTOR_Y != 1.0:
            scale_x = GLOBAL_SCALE_FACTOR_X
            scale_y = GLOBAL_SCALE_FACTOR_Y
            # Застосовуємо трансформацію до ВСЬОГО вмісту
            canvas.scale("all", 0, 0, scale_x, scale_y)
        canvas.after(50, _update_minimap_viewport)

        actual_bbox = canvas.bbox("all")
        if actual_bbox:
            canvas.config(
                scrollregion=(actual_bbox[0] - 50, actual_bbox[1] - 50, actual_bbox[2] + 50, actual_bbox[3] + 50))
        else:
            canvas.config(scrollregion=(0, 0, 800, 800))
    # --- 4.4. Обробники Drag & Drop (Блоки та Стрілки) ---

    def _on_block_drag_start(event):
        """Викликається при натисканні ЛКМ на полотні."""
        # Отримуємо "абсолютні" координати на полотні (з урахуванням прокрутки)
        x_canvas_offset = canvas.canvasx(0)
        y_canvas_offset = canvas.canvasy(0)
        abs_x = event.x + x_canvas_offset
        abs_y = event.y + y_canvas_offset

        # Збільшуємо зону пошуку для легшого "попадання"
        overlapping = canvas.find_overlapping(abs_x - 7, abs_y - 7, abs_x + 7, abs_y + 7)

        # --- 1. Пріоритет: ПЕРЕВІРКА ТОЧКИ СТРІЛКИ ---
        arrow_id = None
        for obj_id in reversed(overlapping):  # Шукаємо згори вниз
            tags = canvas.gettags(obj_id)
            if "flow_arrow" in tags:
                arrow_id = obj_id
                arrow_coords = list(canvas.coords(arrow_id))

                # Перевіряємо, чи клік знаходиться близько до однієї з вершин стрілки
                point_index = -1
                for i in range(len(arrow_coords) // 2):
                    px, py = arrow_coords[i * 2], arrow_coords[i * 2 + 1]
                    if abs(px - abs_x) < 15 and abs(py - abs_y) < 15:
                        point_index = i
                        break

                if point_index != -1:
                    # Знайшли точку стрілки! Починаємо її редагування.
                    drag_data["arrow_id"] = arrow_id
                    drag_data["point_index"] = point_index
                    drag_data["x"] = abs_x
                    drag_data["y"] = abs_y
                    canvas.config(cursor="hand2")

                    # Зберігаємо поточні координати для редагування
                    arrow_data["id"] = arrow_id
                    arrow_data["coords"] = arrow_coords

                    # Візуалізуємо точки (червоні кола)
                    _draw_arrow_points_for_edit(arrow_id, arrow_coords)
                    canvas.tag_raise(arrow_id)
                    canvas.tag_raise("arrow_edit_point")
                    return  # Виходимо, пріоритет у стрілки

        # --- 2. ПЕРЕВІРКА БЛОКУ (якщо стрілка не знайдена) ---
        block_id = None;
        group_tag = None
        for obj_id in reversed(overlapping):
            tags = canvas.gettags(obj_id)
            # Шукаємо фігуру ("block"), але не її текст ("block_text")
            if "block" in tags and "block_text" not in tags:
                block_id = obj_id
                # Знаходимо унікальний тег групи
                for tag in tags:
                    if tag.startswith(("sub_", "rect_", "rhombus_", "ell_", "para_", "hex_")):
                        group_tag = tag
                        break
                if group_tag: break

        if block_id and group_tag:
            # Знайшли блок! Починаємо його перетягування.
            drag_data["item"] = (block_id, group_tag)
            drag_data["x"] = abs_x;
            drag_data["y"] = abs_y
            canvas.config(cursor="hand2")
            canvas.tag_raise(group_tag)  # Блок та його текст/лінії поверх інших
        else:
            # Клікнули на порожньому місці
            drag_data["item"] = None

    def _on_block_drag_move(event):
        """Викликається при русі миші з затиснутою ЛКМ."""

        # --- 1. РУХ ТОЧКИ СТРІЛКИ ---
        if drag_data["arrow_id"] is not None:
            _on_arrow_point_drag_move(event)
            return

        # --- 2. РУХ БЛОКУ ---
        if drag_data["item"]:
            x_canvas_offset = canvas.canvasx(0)
            y_canvas_offset = canvas.canvasy(0)
            current_abs_x = event.x + x_canvas_offset
            current_abs_y = event.y + y_canvas_offset
            block_id, group_tag = drag_data["item"]

            # Розрахунок зсуву (delta)
            dx = current_abs_x - drag_data["x"]
            dy = current_abs_y - drag_data["y"]

            # 2.1. Переміщення самого блоку (всіх елементів з group_tag)
            canvas.move(group_tag, dx, dy)

            # 2.2. Ручне переміщення *приєднаних* до блоку стрілок
            if group_tag in BLOCK_TO_ARROWS:
                for arrow_id in BLOCK_TO_ARROWS[group_tag]:
                    arrow_id_int = int(arrow_id)
                    coords = list(canvas.coords(arrow_id_int))
                    num_points = len(coords) // 2
                    conn = ARROW_CONNECTIONS.get(arrow_id_int, {})

                    is_source = (conn.get('source_tag') == group_tag)
                    is_target = (conn.get('target_tag') == group_tag)

                    new_coords = []
                    for i in range(num_points):
                        point_x = coords[i * 2]
                        point_y = coords[i * 2 + 1]

                        # Рухаємо лише ті точки, які прив'язані до *цього* блоку
                        if (i == 0 and is_source) or \
                                (i == num_points - 1 and is_target):
                            new_coords.extend([point_x + dx, point_y + dy])

                        # (Проміжні точки рухаються, якщо хоча б один кінець приєднаний)
                        elif i > 0 and i < num_points - 1 and (is_source or is_target):
                            new_coords.extend([point_x + dx, point_y + dy])

                        # (Інший кінець стрілки, приєднаний до іншого блоку, не рухається)
                        else:
                            new_coords.extend([point_x, point_y])

                    if new_coords:
                        canvas.coords(arrow_id_int, *new_coords)

            # 2.3. Оновлення останніх координат
            drag_data["x"] = current_abs_x
            drag_data["y"] = current_abs_y

    def _on_block_drag_release(event):
        """Викликається при відпусканні ЛКМ."""

        # --- 1. ЗВІЛЬНЕННЯ ТОЧКИ СТРІЛКИ ---
        if drag_data["arrow_id"] is not None:
            _on_arrow_point_drag_release()
            return

        # --- 2. ЗВІЛЬНЕННЯ БЛОКУ (з "прилипанням" до сітки) ---
        item_data = drag_data["item"]
        if item_data:
            block_id, group_tag = item_data
            canvas.update_idletasks()
            bbox = canvas.bbox(block_id)
            if not bbox:
                drag_data["item"] = None;
                canvas.config(cursor="");
                return

            x0, y0, x1, y1 = bbox
            current_center_x = (x0 + x1) / 2
            current_center_y = (y0 + y1) / 2

            # 2.1. Розрахунок "прилипання" до сітки
            new_x_center = round(current_center_x / GRID_SIZE) * GRID_SIZE
            new_y_center = round(current_center_y / GRID_SIZE) * GRID_SIZE

            final_dx = new_x_center - current_center_x
            final_dy = new_y_center - current_center_y

            if final_dx != 0 or final_dy != 0:
                # 2.2. Застосовуємо зсув до блоку
                canvas.move(group_tag, final_dx, final_dy)

                # 2.3. Застосовуємо той самий зсув до приєднаних стрілок
                if group_tag in BLOCK_TO_ARROWS:
                    for arrow_id in BLOCK_TO_ARROWS[group_tag]:
                        arrow_id_int = int(arrow_id)
                        coords = list(canvas.coords(arrow_id_int))
                        num_points = len(coords) // 2
                        conn = ARROW_CONNECTIONS.get(arrow_id_int, {})
                        is_source = (conn.get('source_tag') == group_tag)
                        is_target = (conn.get('target_tag') == group_tag)

                        new_coords = []
                        for i in range(num_points):
                            point_x = coords[i * 2];
                            point_y = coords[i * 2 + 1]

                            # (Логіка ідентична _on_block_drag_move)
                            if (i == 0 and is_source) or (i == num_points - 1 and is_target):
                                new_coords.extend([point_x + final_dx, point_y + final_dy])
                            elif i > 0 and i < num_points - 1 and (is_source or is_target):
                                new_coords.extend([point_x + final_dx, point_y + final_dy])
                            else:
                                new_coords.extend([point_x, point_y])

                        if new_coords:
                            canvas.coords(arrow_id_int, *new_coords)

        # Скидання стану
        drag_data["item"] = None
        canvas.config(cursor="")
        _update_minimap_viewport()

    def _on_arrow_point_drag_release():
        """Відпускання точки стрілки (прив'язка або вирівнювання)."""
        if drag_data["arrow_id"] is None: return

        arrow_id = drag_data["arrow_id"]
        point_index = drag_data["point_index"]
        coords = arrow_data["coords"]  # (Координати оновлювалися в _on_arrow_point_drag_move)
        coords_index = point_index * 2
        total_points = len(coords) // 2
        i_x = coords[coords_index]
        i_y = coords[coords_index + 1]
        arrow_id_int = int(arrow_id)

        is_end_point = (point_index == 0 or point_index == total_points - 1)
        is_source = (point_index == 0)

        # --- 1. ЛОГІКА ПРИВ'ЯЗКИ (тільки для кінцевих точок) ---
        if is_end_point:
            # Шукаємо, чи є порт блоку під курсором
            snap_point, block_tag, _ = _snap_to_closest_block_point(canvas, i_x, i_y)
            current_conn = ARROW_CONNECTIONS.get(arrow_id_int, {'source_tag': None, 'target_tag': None})

            if block_tag:
                # 1.1. Стрілка ПРИЛИПЛА
                coords[coords_index] = snap_point[0]
                coords[coords_index + 1] = snap_point[1]
                canvas.coords(arrow_id, *coords)

                # 1.2. Оновлюємо логіку зв'язків
                if is_source:
                    _update_arrow_mapping(arrow_id_int, source_tag=block_tag, target_tag=current_conn['target_tag'])
                else:
                    _update_arrow_mapping(arrow_id_int, source_tag=current_conn['source_tag'], target_tag=block_tag)

                # Завершуємо редагування
                canvas.delete("arrow_edit_point");
                drag_data["arrow_id"] = None
                drag_data["point_index"] = -1;
                canvas.config(cursor="");
                return
            else:
                # 1.3. Стрілка ВІДЛИПЛА (Явно розриваємо зв'язок)
                if is_source:
                    _update_arrow_mapping(arrow_id_int, source_tag=False, target_tag=None)
                else:
                    _update_arrow_mapping(arrow_id_int, source_tag=None, target_tag=False)

        # --- 2. ЛОГІКА ВИРІВНЮВАННЯ (для проміжних точок або відлиплих кінців) ---

        # 2.1. Ортогональне вирівнювання (90°)
        is_orthogonal_snapped = False
        neighbors = []  # (Сусідні точки стрілки)
        if point_index > 0:
            prev_idx = (point_index - 1) * 2;
            neighbors.append((coords[prev_idx], coords[prev_idx + 1]))
        if point_index < (len(coords) // 2) - 1:
            next_idx = (point_index + 1) * 2;
            neighbors.append((coords[next_idx], coords[next_idx + 1]))

        # Шукаємо найближчу вертикальну/горизонтальну лінію до сусідів
        min_distance = SNAP_THRESHOLD;
        best_snap_dx = 0;
        best_snap_dy = 0
        for neighbor_x, neighbor_y in neighbors:
            dx_v = neighbor_x - i_x  # (Відстань до вертикалі)
            if abs(dx_v) < min_distance:
                min_distance = abs(dx_v);
                best_snap_dx = dx_v;
                best_snap_dy = 0;
                is_orthogonal_snapped = True

            dy_h = neighbor_y - i_y  # (Відстань до горизонталі)
            if abs(dy_h) < min_distance:
                min_distance = abs(dy_h);
                best_snap_dx = 0;
                best_snap_dy = dy_h;
                is_orthogonal_snapped = True

        if is_orthogonal_snapped:
            coords[coords_index] += best_snap_dx
            coords[coords_index + 1] += best_snap_dy
        else:
            # 2.2. Прилипання до сітки (якщо ортогональне не спрацювало)
            snap_x = round(coords[coords_index] / ARROW_GRID_SIZE) * ARROW_GRID_SIZE
            snap_y = round(coords[coords_index + 1] / ARROW_GRID_SIZE) * ARROW_GRID_SIZE
            coords[coords_index] = snap_x
            coords[coords_index + 1] = snap_y

        canvas.coords(arrow_id, *coords)

        # Загальне очищення
        canvas.delete("arrow_edit_point");
        drag_data["arrow_id"] = None
        drag_data["point_index"] = -1;
        canvas.config(cursor="")

    def _draw_arrow_points_for_edit(arrow_id, coords):
        """Малює червоні кола на вершинах стрілки для редагування."""
        canvas.delete("arrow_edit_point")
        for i in range(len(coords) // 2):
            x, y = coords[i * 2], coords[i * 2 + 1]
            canvas.create_oval(x - 5, y - 5, x + 5, y + 5, fill="red",
                               tags=("arrow_edit_point", f"arrow_point_{i}"))

    def _on_arrow_point_drag_move(event):
        """Рух точки стрілки (поки ЛКМ затиснута)."""
        if drag_data["arrow_id"] is not None:
            current_abs_x = canvas.canvasx(event.x)
            current_abs_y = canvas.canvasy(event.y)
            arrow_id = drag_data["arrow_id"]
            point_index = drag_data["point_index"]

            dx = current_abs_x - drag_data["x"]
            dy = current_abs_y - drag_data["y"]

            # Оновлюємо координати в тимчасовому списку
            coords = arrow_data["coords"]
            coords_index = point_index * 2
            coords[coords_index] += dx
            coords[coords_index + 1] += dy

            # Оновлюємо стрілку на полотні
            canvas.coords(arrow_id, *coords)

            # Оновлюємо позицію для наступного руху
            drag_data["x"] = current_abs_x
            drag_data["y"] = current_abs_y

            # Перемальовуємо червоні точки
            _draw_arrow_points_for_edit(arrow_id, coords)

    def update_colors_wrapper(*args):
        """Обгортка для оновлення кольорів (викликається при зміні полів)."""
        colors = (ellipse_color_var.get(), rect_color_var.get(), rhombus_color_var.get(),
                  sub_color_var.get(), hex_color_var.get())
        _update_colors_only(canvas, colors)

    def toggle_grid_closure():
        """Обгортка для перемикання сітки (для чекбоксу)."""
        _toggle_grid(canvas, grid_visible_var.get())

    # --- 5. СТВОРЕННЯ ВІДЖЕТІВ GUI ---

    # 5.1. Міні-карта (створюється тут, але розміщується в _toggle_minimap)
    minimap_frame = tk.Frame(canvas_frame, width=MINIMAP_W, height=MINIMAP_H,
                             bd=1, relief="sunken", bg="white")
    minimap_frame.pack_propagate(False)
    minimap_canvas = tk.Canvas(minimap_frame, bg="white")
    minimap_canvas.pack(fill=tk.BOTH, expand=1)
    minimap_canvas.create_rectangle(0, 0, 1, 1, outline="red", width=2, tags="viewport");

    # 5.2. Панель керування (Control Frame)
    dropdown = tk.OptionMenu(control_frame, selected_func, *function_names, command=update_drawing)
    dropdown.pack(side=tk.LEFT, padx=5, anchor="n")

    scale_frame = tk.Frame(control_frame);
    scale_frame.pack(side=tk.LEFT, padx=10)

    tk.Label(scale_frame, text="Ширина:").pack(side=tk.LEFT)
    h_slider = tk.Scale(scale_frame, from_=0.1, to=5.0, resolution=0.1, orient=tk.HORIZONTAL, variable=h_scale_var);
    h_slider.pack(side=tk.LEFT)
    h_entry = ttk.Entry(scale_frame, width=5, textvariable=h_scale_var);
    h_entry.pack(side=tk.LEFT, padx=(2, 10))

    tk.Label(scale_frame, text="Висота:").pack(side=tk.LEFT, padx=(10, 0))
    v_slider = tk.Scale(scale_frame, from_=0.1, to=5.0, resolution=0.1, orient=tk.HORIZONTAL, variable=v_scale_var);
    v_slider.pack(side=tk.LEFT)
    v_entry = ttk.Entry(scale_frame, width=5, textvariable=v_scale_var);
    v_entry.pack(side=tk.LEFT, padx=(2, 10))

    tk.Label(scale_frame, text="Зсув циклів:").pack(side=tk.LEFT, padx=(10, 0))
    loop_slider = tk.Scale(scale_frame, from_=0, to=100.0, resolution=0.1, orient=tk.HORIZONTAL,
                           variable=loop_offset_var);
    loop_slider.pack(side=tk.LEFT)
    loop_entry = ttk.Entry(scale_frame, width=5, textvariable=loop_offset_var);
    loop_entry.pack(side=tk.LEFT, padx=(2, 10))

    tk.Label(scale_frame, text="Зсув IF:").pack(side=tk.LEFT, padx=(10, 0))
    if_slider = tk.Scale(scale_frame, from_=0.01, to=10.0, resolution=0.01, orient=tk.HORIZONTAL,
                         variable=if_offset_var);
    if_slider.pack(side=tk.LEFT)
    if_entry = ttk.Entry(scale_frame, width=5, textvariable=if_offset_var);
    if_entry.pack(side=tk.LEFT, padx=(2, 10))

    # 5.3. Панель кольорів (Color Frame)
    tk.Label(color_frame, text="Start:").pack(side=tk.LEFT, padx=(5, 0));
    ttk.Entry(color_frame, width=8, textvariable=ellipse_color_var).pack(side=tk.LEFT, padx=(2, 5))
    tk.Label(color_frame, text="Rect:").pack(side=tk.LEFT, padx=(5, 0));
    ttk.Entry(color_frame, width=8, textvariable=rect_color_var).pack(side=tk.LEFT, padx=(2, 5))
    tk.Label(color_frame, text="If/While:").pack(side=tk.LEFT, padx=(5, 0));
    ttk.Entry(color_frame, width=8, textvariable=rhombus_color_var).pack(side=tk.LEFT, padx=(2, 5))
    tk.Label(color_frame, text="Func:").pack(side=tk.LEFT, padx=(5, 0));
    ttk.Entry(color_frame, width=8, textvariable=sub_color_var).pack(side=tk.LEFT, padx=(2, 5))
    tk.Label(color_frame, text="For:").pack(side=tk.LEFT, padx=(5, 0));
    ttk.Entry(color_frame, width=8, textvariable=hex_color_var).pack(side=tk.LEFT, padx=(2, 5))
    ttk.Checkbutton(color_frame, text="Пропускати Ініціалізацію", variable=skip_init_var).pack(side=tk.LEFT,
                                                                                               padx=(20, 5))
    tk.Label(scale_frame, text="| Zoom:").pack(side=tk.LEFT, padx=(20, 0))
    ttk.Entry(scale_frame, width=6, textvariable=zoom_display_var, state='readonly').pack(side=tk.LEFT, padx=(2, 5))
    # 5.4. Ліва панель (Кнопки)
    tk.Label(left_toolbar_frame, text="Збереження", font=("Arial", 11, "bold")).pack(pady=5)
    tk.Button(left_toolbar_frame, text="Повна БС (.png)", command=save_full_diagram_ps).pack(fill=tk.X, pady=3, padx=7)
    tk.Button(left_toolbar_frame, text="Видима БС (.png)", command=save_visible_diagram_png).pack(fill=tk.X, pady=3,
                                                                                                  padx=7)
    tk.Button(left_toolbar_frame, text="Експорт в .drawio", command=save_as_drawio).pack(fill=tk.X, pady=3, padx=7)
    tk.Button(left_toolbar_frame, text="Псевдокод (.txt)", command=save_pseudocode).pack(fill=tk.X, pady=3, padx=7)

    ttk.Checkbutton(left_toolbar_frame, text="Показати міні-карту", variable=show_minimap_var,
                    command=_toggle_minimap).pack(fill=tk.X, pady=3, padx=7)
    ttk.Checkbutton(left_toolbar_frame, text="Сітка", variable=grid_visible_var,
                    command=toggle_grid_closure).pack(fill=tk.X, padx=5, pady=5)
    ttk.Separator(left_toolbar_frame, orient='horizontal').pack(fill=tk.X, pady=10, padx=5)
    tk.Button(left_toolbar_frame, text="Допомога", command=open_help_window).pack(fill=tk.X, pady=3, padx=7)

    # --- 6. ПРИВ'ЯЗКА ПОДІЙ (BINDING) ---

    # 6.1. Скролбари та Міні-карта
    v_scroll.config(command=lambda *a: (canvas.yview(*a), _update_minimap_viewport()))
    h_scroll.config(command=lambda *a: (canvas.xview(*a), _update_minimap_viewport()))
    minimap_canvas.bind("<Button-1>", _on_minimap_click)
    minimap_canvas.bind("<B1-Motion>", _on_minimap_click)

    # 6.2. Drag & Drop
    canvas.bind("<ButtonPress-1>", _on_block_drag_start);
    canvas.bind("<B1-Motion>", _on_block_drag_move);
    canvas.bind("<ButtonRelease-1>", _on_block_drag_release)

    # 6.3. Панорамування (Pan)
    canvas.bind("<ButtonPress-2>", _on_pan_start);  # (Середня кнопка)
    canvas.bind("<B2-Motion>", _on_pan_move);
    canvas.bind("<ButtonRelease-2>", _on_pan_end)
    canvas.bind("<Shift-ButtonPress-1>", _on_pan_start);  # (Shift + ЛКМ)
    canvas.bind("<Shift-B1-Motion>", _on_pan_move);
    canvas.bind("<Shift-ButtonRelease-1>", _on_pan_end)

    # 6.4. Масштабування (Zoom)
    canvas.bind("<Control-MouseWheel>", _on_mouse_wheel);  # (Windows/Linux)
    canvas.bind("<Control-Button-4>", _on_mouse_wheel);  # (Linux)
    canvas.bind("<Control-Button-5>", _on_mouse_wheel)  # (Linux)

    # 6.5. Прокрутка (Scroll)
    canvas.bind("<MouseWheel>", _on_vertical_scroll);
    canvas.bind("<Button-4>", _on_vertical_scroll);
    canvas.bind("<Button-5>", _on_vertical_scroll)
    canvas.bind("<Shift-MouseWheel>", _on_horizontal_scroll);
    canvas.bind("<Shift-Button-4>", _on_horizontal_scroll);
    canvas.bind("<Shift-Button-5>", _on_horizontal_scroll)

    # 6.6. Оновлення від повзунків/чекбоксів (Trace/Command)
    loop_offset_var.trace_add("write", update_drawing)
    if_offset_var.trace_add("write", update_drawing)
    skip_init_var.trace_add("write", update_drawing)

    # (Для миттєвого оновлення кольорів)
    ellipse_color_var.trace_add("write", update_colors_wrapper);
    rect_color_var.trace_add("write", update_colors_wrapper);
    rhombus_color_var.trace_add("write", update_colors_wrapper);
    sub_color_var.trace_add("write", update_colors_wrapper);
    hex_color_var.trace_add("write", update_colors_wrapper);

    # (Command для Scale, щоб спрацьовувало при русі повзунка)
    h_slider.config(command=update_drawing)
    v_slider.config(command=update_drawing)
    loop_slider.config(command=update_drawing)
    if_slider.config(command=update_drawing)

    # --- 7. ПЕРШИЙ ЗАПУСК ---
    update_drawing()


# --- 10. ЗАПУСК ПРОГРАМИ ТА ЕКСПОРТ В DRAW.IO ---

def select_file_and_read_words_v30(root):
    """
    Головна функція запуску:
    1. Відкриває діалог вибору файлу.
    2. Читає C-код.
    3. Запускає токенізацію та парсинг.
    4. Запускає вікно GUI (draw_flowchart_window).
    """
    global FUNCTION_CODE_MAP
    FUNCTION_CODE_MAP = {}

    file_path = filedialog.askopenfilename(
        title="Select a C or Text File",
        filetypes=(
            ("C and Text files", "*.c *.txt"),
            ("C files", "*.c"),
            ("Text files", "*.txt"),
            ("All files", "*.*")
        )
    )

    if file_path:
        print(f"File selected: {file_path}")
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                full_text = f.read()

            # --- PREPROCESSING (Tokenization) ---
            # (Ця логіка дублює 'tokenize_code', але працює)
            full_text = re.sub(r'//.*', '', full_text)  # Видалення // коментарів

            # Додавання пробілів навколо операторів
            symbols_to_separate = ['(', ')', '[', ']', ';', '=', ',', '&', '<', '>', '!', '+', '-', '{', '}', '|']
            for symbol in symbols_to_separate:
                full_text = full_text.replace(symbol, f' {symbol} ')
            full_text = full_text.replace('!=', ' != ')
            full_text = full_text.replace('==', ' == ')
            full_text = full_text.replace('<=', ' <= ')
            full_text = full_text.replace('>=', ' >= ')
            full_text = full_text.replace('++', ' ++ ')
            full_text = full_text.replace('--', ' -- ')
            full_text = full_text.replace('+=', ' += ')
            full_text = full_text.replace('-=', ' -= ')
            full_text = full_text.replace('||', ' || ')
            full_text = full_text.replace('&&', ' && ')

            symbols = ['(', ')', '{', '}', ';', ',', '=', '+', '-', '*', '/', '>', '<', '!']
            for sym in symbols:
                full_text = full_text.replace(sym, f' {sym} ')

            tokens = full_text.split()

            # Фільтрація /* ... */ та #define
            filtered_tokens = []
            in_comment = False
            in_define = False
            for token in tokens:
                if token.startswith('/*'): in_comment = True; continue
                if token.endswith('*/'): in_comment = False; continue
                if token.startswith('#'): in_define = True; continue
                if in_define and token.endswith('\\'): continue
                if in_define and not token.endswith('\\'): in_define = False; continue
                if not in_comment and not in_define:
                    filtered_tokens.append(token)

            word_list = filtered_tokens  # (Це фінальний список токенів)
            # --- END PREPROCESSING ---

            # Знаходимо всі функції в коді
            function_map = find_function_bodies(word_list)

            # Парсимо тіло кожної знайденої функції
            FUNCTION_CODE_MAP = {}
            for func_name, data in function_map.items():
                try:
                    tokens = data["body"]
                    arg_tokens = data["args"]

                    # Запускаємо парсер C -> Псевдокод
                    parsed_list = parse_token_list(tokens, depth=0)

                    final_list = []
                    arg_string = " ".join(arg_tokens)
                    if len(arg_string) > 30:
                        arg_string = arg_string[:27] + "..."

                    # Додаємо "Початок" та "Кінець"
                    if func_name == "main":
                        final_list.append("Початок")
                        final_list.extend(parsed_list)
                        final_list.append("Кінець")
                    else:
                        final_list.append(f"Початок: {func_name}({arg_string})")
                        final_list.extend(parsed_list)
                        final_list.append(f"Кінець: {func_name}({arg_string})")

                    FUNCTION_CODE_MAP[func_name] = final_list
                except Exception as e_inner:
                    print(f"Error while parsing function '{func_name}': {e_inner}")

            print("Parsing complete. Launching flowchart viewer...")
            # Запускаємо GUI
            draw_flowchart_window(root, FUNCTION_CODE_MAP)

        except FileNotFoundError:
            print(f"Error: File not found at {file_path}")
        except Exception as e:
            # (Обробка помилок парсингу)
            pass
    else:
        print("No file was selected.")
        root.destroy()


# --- 10.1. Логіка експорту в DRAW.IO XML ---

DRAWIO_HEADER = """<mxfile host="app.diagrams.net">
  <diagram id="DIAGRAM_ID" name="{page_name}">
    <mxGraphModel dx="1400" dy="800" grid="1" gridSize="10" guides="1" tooltips="1" connect="1" arrows="1" fold="1" page="1" pageScale="1" pageWidth="850" pageHeight="1100" math="0" shadow="0">
      <root>
        <mxCell id="0" />
        <mxCell id="1" parent="0" />
"""
DRAWIO_FOOTER = """
      </root>
    </mxGraphModel>
  </diagram>
</mxfile>
"""
# Співвідношення стилів tkinter та draw.io
STYLES = {
    "ellipse": "ellipse;whiteSpace=wrap;html=1;fillColor=#FFD1DC;strokeColor=#000000;",
    "rect": "rounded=0;whiteSpace=wrap;html=1;fillColor=#ADD8E6;strokeColor=#000000;",
    "rhombus": "rhombus;whiteSpace=wrap;html=1;fillColor=#FFFFE0;strokeColor=#000000;",
    "sub": "shape=process;whiteSpace=wrap;html=1;fillColor=#CCEEFF;strokeColor=#000000;",
    "hex": "shape=hexagon;perimeter=hexagonPerimeter2;whiteSpace=wrap;html=1;fillColor=#D8BFD8;strokeColor=#000000;",
    "para": "shape=parallelogram;perimeter=parallelogramPerimeter;whiteSpace=wrap;html=1;fillColor=#CCEEFF;strokeColor=#000000;",
    "arrow": "edgeStyle=orthogonalEdgeStyle;rounded=0;html=1;endArrow=classic;strokeColor=#000000;"
}


def _calculate_relative_point(abs_x, abs_y, bbox):
    """
    Перетворює абсолютні координати (кінці стрілки) на відносні (0..1)
    відносно меж (bbox) батьківського блоку (для draw.io).
    """
    x0, y0, x1, y1 = bbox
    width = x1 - x0;
    height = y1 - y0
    if width == 0 or height == 0: return 0.5, 0.5  # (Центр за замовчуванням)

    rel_x = max(0.0, min(1.0, (abs_x - x0) / width))
    rel_y = max(0.0, min(1.0, (abs_y - y0) / height))
    return rel_x, rel_y


def _xml_arrow(id_num, source_id, target_id, text=""):
    """(Не використовується) Створює XML для простої стрілки."""
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\"", "&quot;").replace("'",
                                                                                                                "&apos;")
    return (
        f'        <mxCell id="{id_num}" value="{text}" style="{STYLES["arrow"]}" edge="1" parent="1" source="{source_id}" target="{target_id}">\n'
        f'          <mxGeometry relative="1" as="geometry"/>\n'
        f'        </mxCell>\n'
    )


def _xml_arrow_with_waypoints(id_num, source_id, target_id, text="", waypoint_coords=None,
                              source_x_rel=None, source_y_rel=None, target_x_rel=None, target_y_rel=None):
    """Створює XML для стрілки (з проміжними точками та точками прив'язки)."""
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\"", "&quot;").replace("'",
                                                                                                                "&apos;")
    # Проміжні точки
    waypoint_geometry = ""
    if waypoint_coords and len(waypoint_coords) > 4:  # (Якщо є хоча б 1 проміжна точка)
        points_list = []
        for i in range(2, len(waypoint_coords) - 2, 2):
            x = waypoint_coords[i];
            y = waypoint_coords[i + 1]
            points_list.append(f'<mxPoint x="{x:.2f}" y="{y:.2f}"/>')
        if points_list:
            waypoint_geometry = f'<Array as="points">\n' + '  '.join(points_list) + '\n        </Array>'

    # Відносні точки прив'язки (sourcePoint, targetPoint)
    terminal_points = ""
    if source_x_rel is not None:
        terminal_points += f'<mxPoint x="{source_x_rel:.4f}" y="{source_y_rel:.4f}" as="sourcePoint"/>'
    if target_x_rel is not None:
        terminal_points += f'<mxPoint x="{target_x_rel:.4f}" y="{target_y_rel:.4f}" as="targetPoint"/>'

    return (
        f'        <mxCell id="{id_num}" value="{text}" style="{STYLES["arrow"]}" edge="1" parent="1" source="{source_id}" target="{target_id}">\n'
        f'          <mxGeometry relative="1" as="geometry">\n'
        f'             {terminal_points}\n'
        f'             {waypoint_geometry}\n'
        f'          </mxGeometry>\n'
        f'        </mxCell>\n'
    )


def _xml_block(id_num, text, style_key, x, y, w, h):
    """Створює XML для блоку (Vertex)."""
    text_content = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\"", "&quot;").replace(
        "'", "&apos;")
    style = STYLES.get(style_key, STYLES["rect"])

    return (
        f'        <mxCell id="{id_num}" value="{text_content}" style="{style}" vertex="1" parent="1">\n'
        f'          <mxGeometry x="{x:.2f}" y="{y:.2f}" width="{w:.2f}" height="{h:.2f}" as="geometry"/>\n'
        f'        </mxCell>\n'
    )


def generate_drawio_xml_from_canvas(canvas, page_name="Page-1"):
    """
    Генерує XML-файл .drawio на основі поточного стану полотна.

    Використовує BLOCK_TEXT_MAP для тексту та ARROW_CONNECTIONS для зв'язків.
    """
    global ARROW_CONNECTIONS
    global BLOCK_TEXT_MAP

    id_counter = 10
    xml_elements = []
    group_tag_to_data = {}  # {group_tag: {"id": drawio_id, "bbox": ...}}

    canvas.update_idletasks()

    # 1. Фаза 1: Обробка БЛОКІВ
    # (Використовуємо find_withtag, щоб ігнорувати сітку/порти)
    all_blocks = canvas.find_withtag("block")

    for obj_id in all_blocks:
        tags = canvas.gettags(obj_id)
        if "block_text" in tags: continue  # Ігноруємо текст, нам потрібні фігури

        # Знаходимо унікальний тег групи (напр. "rect_400_50")
        potential_group_tag = next(
            (tag for tag in tags if tag.startswith(("sub_", "rect_", "rhombus_", "ell_", "para_", "hex_"))), None)

        if potential_group_tag and potential_group_tag not in group_tag_to_data:
            bbox = canvas.bbox(obj_id)
            if not bbox: continue

            # Визначаємо стиль draw.io
            style_map = {"ellipse": "ell_", "rect": "rect_", "rhombus": "rhombus_", "sub": "sub_", "hex": "hex_",
                         "para": "para_"}
            style_key = next((key for key, val in style_map.items() if potential_group_tag.startswith(val)), "rect")

            # ❗️ (Ключовий момент) Беремо текст з BLOCK_TEXT_MAP за тегом.
            text_content = BLOCK_TEXT_MAP.get(potential_group_tag, style_key.capitalize())

            drawio_id = f"block-{id_counter}";
            id_counter += 1
            x0, y0, x1, y1 = bbox
            w = x1 - x0;
            h = y1 - y0

            group_tag_to_data[potential_group_tag] = {"id": drawio_id, "bbox": bbox}
            xml_elements.append(_xml_block(drawio_id, text_content, style_key, x0, y0, w, h))

    # 2. Фаза 2: Обробка СТРІЛОК
    for arrow_id, conn_data in ARROW_CONNECTIONS.items():
        source_tag = conn_data.get('source_tag')
        target_tag = conn_data.get('target_tag')

        # (Переконуємось, що обидва кінці стрілки прив'язані)
        if not (source_tag and target_tag and \
                source_tag in group_tag_to_data and \
                target_tag in group_tag_to_data):
            continue

        source_id = group_tag_to_data[source_tag]["id"]
        target_id = group_tag_to_data[target_tag]["id"]

        arrow_coords = canvas.coords(arrow_id)
        if len(arrow_coords) < 4: continue

        x_start, y_start = arrow_coords[0], arrow_coords[1]
        x_end, y_end = arrow_coords[-2], arrow_coords[-1]

        # Розрахунок відносних точок (для draw.io)
        source_x_rel, source_y_rel = _calculate_relative_point(x_start, y_start, group_tag_to_data[source_tag]["bbox"])
        target_x_rel, target_y_rel = _calculate_relative_point(x_end, y_end, group_tag_to_data[target_tag]["bbox"])

        drawio_id = f"arrow-{id_counter}";
        id_counter += 1
        arrow_text = ""  # (Текст "True/False" ще не реалізований для експорту)

        xml_elements.append(_xml_arrow_with_waypoints(
            drawio_id, source_id, target_id, text=arrow_text,
            waypoint_coords=arrow_coords,
            source_x_rel=source_x_rel, source_y_rel=source_y_rel,
            target_x_rel=target_x_rel, target_y_rel=target_y_rel
        ))

    # 3. Збірка XML
    body = "".join(xml_elements)
    return DRAWIO_HEADER.format(page_name=page_name) + body + DRAWIO_FOOTER


# --- 11. ТОЧКА ВХОДУ ---

if __name__ == "__main__":
    main_root = tk.Tk()
    main_root.withdraw()  # Ховаємо головне (порожнє) вікно Tk
    main_root.attributes('-topmost', True)  # (Для діалогу вибору файлу)

    # Запускаємо головну логіку
    select_file_and_read_words_v30(main_root)

    print("Запуск головного циклу Tkinter. Закрийте вікно схеми для виходу.")
    main_root.mainloop()  # Запускаємо цикл подій