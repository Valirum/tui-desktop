#!/home/ap/SPO/kursach/venv/bin/python3
import curses
import pty
import os
import subprocess
import psutil
import threading
import select
import termios
import struct
import time
import pyte
import signal
import fcntl
import sys
import calendar
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont
import json

# Глобальные переменные
tab_threads = {}  # tab_index -> threading.Thread
tab_stop_events = {}  # tab_index -> threading.Event
tabs = []          # Список вкладок
current_tab = 0    # Индекс текущей вкладки
prefix_active = False  # True, если ожидаем после Ctrl+B
input_buffer = ""  # Буфер ввода для главной вкладки
force_render = 0
mouse_enabled = True  # Поддержка мыши
hovered_menu_item = -1  # Индекс элемента меню, над которым находится мышь
hovered_tab = -10  # Индекс вкладки, над которой находится мышь
battary_metrics = 0
prev_curs_y = [0, 0] # Для обновления строчки, с которой ушёл курсор

desktop_mode = "normal"  # или "favorite"
navigation_stack = []    # [(mode, path), ...]

DEBUG_LAST_KEY_CODE = 0

with open("ascii_cache.json", "r") as f:
    background_image_cache = json.load(f)

with open("config.json", "r") as f:
    config = json.load(f)

iterations = 0

def get_bg_icon_list(bg_image_dir = './bg_images/'):
    bg_image_files = []

    try:
        for filename in os.listdir(bg_image_dir):
            # Проверяем, является ли файл изображением (по расширению)
            if filename.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.bmp', '.tga', '.tiff', '.webp')):
                full_path = os.path.join(bg_image_dir, filename)
                if os.path.isfile(full_path):  # Убедимся, что это файл, а не подкаталог
                    bg_image_files.append(full_path)
        return bg_image_files
    except FileNotFoundError:
        print(f"Директория {bg_image_dir} не найдена.")
        return []
    except PermissionError:
        print(f"Нет доступа к директории {bg_image_dir}.")
        return []
    except Exception as e:
        print(f"Ошибка при чтении директории {bg_image_dir}: {e}")
        return []

bg_image_files = [None]+get_bg_icon_list()
current_bg_image = None

def toggle_active_prefix():
    global prefix_active
    prefix_active = not prefix_active

def toggle_battary_metrix():
    global battary_metrics
    battary_metrics = not battary_metrics

def get_battery_percentage():
    """Получает процент заряда батареи (для Linux)"""
    try:
        # Попытка получить заряд через sysfs
        with open('/sys/class/power_supply/BAT0/capacity', 'r') as f:
            return int(f.read().strip())
    except:
        try:
            # Альтернативный способ через upower
            result = subprocess.run(['upower', '-i', '/org/freedesktop/UPower/devices/battery_BAT0'], 
                                  capture_output=True, text=True)
            for line in result.stdout.split('\n'):
                if 'percentage:' in line:
                    return int(line.split(':')[1].strip().replace('%', ''))
        except:
            pass
    return -1  # Батарея не найдена или ошибка

def get_battery_icon(percentage):
    """Возвращает иконку батареи в зависимости от процента заряда"""
    if percentage < 0:
        return "[-----]"
    elif percentage < 10:
        return "[■□□□□]"
    elif percentage < 30:
        return "[■■□□□]"
    elif percentage < 50:
        return "[■■■□□]"
    elif percentage < 70:
        return "[■■■■□]"
    elif percentage < 90:
        return "[■■■■■]"
    else:
        return "[■■■■■]"

prefix_items = [{
    "str":lambda: "[MENU]",
    "click": lambda win, twin: show_menu_dialog(win)
},
{
    "str":lambda: " [+] ",
    "click": lambda win, twin: show_new_tab_dialog(win, twin)
}
]
postfix_items = [
{
    "str": lambda: f"{DEBUG_LAST_KEY_CODE}",
    "click": lambda *args: toggle_active_prefix()
},
{
    "str": lambda: " !!! " if prefix_active else " --- ",
    "click": lambda *args: toggle_active_prefix()
},
{
    "str": lambda: f" {get_battery_icon(get_battery_percentage())} " if battary_metrics else f"  {get_battery_percentage()}%  ",
    "click": lambda *args: toggle_battary_metrix()
},
{
    "str": lambda: f'{time.strftime("[%d.%m.%y | %H:%M:%S]")}',
    "click": lambda win, twin: show_calendar_dialog(win)
},
{
    "str": lambda: f'',                            #for gebug only
    "click": lambda win, twin: show_calendar_dialog(win)
}
]


# Список программ, которые НЕ поддерживают мышь
mouse_blacklist = [
    "fastfetch",
    "neofetch",
    "ls",
    "cat",
    "echo",
    "pwd",
    "whoami",
    "date",
    "cal",
    "figlet",
    "cowsay",
    "htop",
    "top",
    "btop",
    "nano"
]

# Список программ, которые поддерживают мышь
mouse_whitelist = [
    "micro",
    "vim",
    "nvim",
    "less",
    "more",
    "tmux",
    "screen"
]

# Глобальные переменные для рабочего стола
desktop_items = []
hovered_desktop_item = -1
desktop_grid = {}  # y, x -> item_index
current_directory = "."  # Текущая директория


def create_desktop_icon(icon_type, name):
    icons = {
        'folder': {
            'graphic': [' ┌─────┐ ', ' │■■■■■│ ', ' │■   ■│ ', ' │■■■■■│ ', ' └─────┘ '],
            'symbol': '📁'
        },
        'file': {
            'graphic': [' ┌─────┐ ', ' │     │ ', ' │ ... │ ', ' │     │ ', ' └─────┘ '],
            'symbol': '📄'
        },
        'executable': {
            'graphic': [' ┌─────┐ ', ' │  $  │ ', ' │ ### │ ', ' │ ### │ ', ' └─────┘ '],
            'symbol': '⚙️'
        },
        'image': {
            'graphic': [' ┌─────┐ ', ' │┌───┐│ ', ' ││■■■││ ', ' │└───┘│ ', ' └─────┘ '],
            'symbol': '🖼️'
        },
        'root': {
            'graphic': [' ┌─────┐ ', ' │ --- │ ', ' │  /  │ ', ' │ --- │ ', ' └─────┘ '],
            'symbol': '⚡'
        },
        'home': {
            'graphic': [' ┌─────┐ ', ' │ --- │ ', ' │  ~  │ ', ' │ --- │ ', ' └─────┘ '],
            'symbol': '🏠'
        }
    }
    icon_data = icons.get(icon_type, icons['file'])
    clean_name = name.replace('.sh', '')
    lines = []
    for i in range(0, len(clean_name), 7):
        lines.append(clean_name[i:i+7])
        if len(lines) >= 2:
            break
    while len(lines) < 2:
        lines.append('')
    return {
        'graphic': icon_data['graphic'],
        'label_lines': lines,
        'symbol': icon_data['symbol']
    }

def is_in_favorite(path):
    fav = config["paths"].get("favorite", {"files": [], "folders": []})
    abs_path = os.path.abspath(path)
    return abs_path in [os.path.abspath(p[0]) for p in fav["files"] + fav["folders"]]

def put_to_grid(workspace_win, item, x, y):
    global desktop_grid
    max_y, max_x = workspace_win.getmaxyx()
    icon_cols = max_x // 9
    icon_rows = max_y // 7

    # Преобразуем координаты в целые числа, если они не "-"
    if x != "-" and y != "-":
        try:
            x = int(x)
            y = int(y)
        except ValueError:
            x = y = "-"

    if x == "-" or y == "-":
        # Ищем первую свободную ячейку
        for i in range(icon_rows):
            for j in range(icon_cols):
                if (i, j) not in desktop_grid:
                    desktop_grid[(i, j)] = item
                    return
        # Если нет свободных — кладём в последнюю (или игнорируем)
        if icon_rows > 0 and icon_cols > 0:
            desktop_grid[(icon_rows - 1, icon_cols - 1)] = item
    else:
        # Фиксированная позиция
        pos = (y, x)

        if pos not in desktop_grid:
            # Ячейка свободна — просто кладём
            desktop_grid[pos] = item
        else:
            # Ячейка занята — вытесняем старый элемент
            displaced_item = desktop_grid[pos]
            desktop_grid[pos] = item

            # Ищем новое место для вытесненного элемента
            for i in range(icon_rows):
                for j in range(icon_cols):
                    if (i, j) not in desktop_grid:
                        desktop_grid[(i, j)] = displaced_item
                        return

            # Если вообще нет свободных ячеек — перезаписываем последнюю
            if icon_rows > 0 and icon_cols > 0:
                desktop_grid[(icon_rows - 1, icon_cols - 1)] = displaced_item
    
def scan_desktop_items(workspace_win):
    global desktop_items, desktop_grid, current_directory, desktop_mode
    desktop_items = []
    desktop_grid = {}

    icons = {
        "/": "root",
        os.path.expanduser("~").split("/")[-1]: "home"
    }

    if desktop_mode == "favorite":
        fav = config["paths"].get("favorite", {"files": [], "folders": []})
        found_root = False
        found_home = False
        
        for path,x,y in fav["folders"]:
            if path == "/":
                found_root = True
                continue
            if path == os.path.expanduser("~"):
                found_home = True
                continue
             
        if not found_root:
            fav["folders"].append(["/","-","-"])
        if not found_home:
            fav["folders"].append([os.path.expanduser("~"),"-","-"])
                    
        
        for path,x,y in fav["folders"]:
            name = os.path.basename(path) or path
            desktop_items.append({
                'name': name,
                'path': path,
                'type': 'folder',
                'icon': create_desktop_icon(icons.get(name, "folder") , name)
            })
            put_to_grid(workspace_win,desktop_items.__len__()-1,x,y)
                
        for path,x,y in fav["files"]:
            name = os.path.basename(path)
            if name.endswith('.sh'):
                icon_type = 'executable'
            elif name.endswith(('.jpg', '.png', '.gif')):
                icon_type = 'image'
            elif name.endswith(('.txt', '.py', '.md', '.conf')):
                icon_type = 'file'
            else:
                icon_type = 'file'
            desktop_items.append({
                'name': name,
                'path': path,
                'type': icon_type,
                'icon': create_desktop_icon(icon_type, name)
            })
            put_to_grid(workspace_win,desktop_items.__len__()-1,x,y)

    else:
        try:
            items = []
            if current_directory != ".":
                items.append("..")
            for item in os.listdir(current_directory):
                if not item.startswith('.'):
                    items.append(item)
            for item_name in items:
                if item_name == "..":
                    item_path = os.path.dirname(current_directory) if current_directory != "." else ".."
                    icon_type = 'folder'
                else:
                    item_path = os.path.join(current_directory, item_name)
                    if os.path.isdir(item_path):
                        icon_type = 'folder'
                    elif item_name.endswith('.sh'):
                        icon_type = 'executable'
                    elif item_name.endswith(('.txt', '.py', '.md', '.conf')):
                        icon_type = 'file'
                    elif item_name.endswith(('.jpg', '.png', '.gif')):
                        icon_type = 'image'
                    else:
                        icon_type = 'file'

                # Проверка на избранное
                in_favorite = is_in_favorite(item_path)

                desktop_items.append({
                    'name': item_name,
                    'path': item_path,
                    'type': icon_type,
                    'in_favorite': in_favorite,  # ← новое поле
                    'icon': create_desktop_icon(icon_type, item_name)
                })
                
        except Exception as e:
            pass

        for i, _ in enumerate(desktop_items):
            put_to_grid(workspace_win, i, "-", "-")

        
def image_to_ascii(image_path, width_in_chars, height_in_chars, char_density=' .:-=+*#%@'):
    """
    Конвертирует изображение в ASCII-арт, подходящий для отображения в консоли.

    Args:
        image_path (str): Путь к изображению.
        width_in_chars (int): Ширина выходного ASCII-арта в символах.
        height_in_chars (int): Высота выходного ASCII-арта в символах.
        char_density (str): Строка символов, отсортированных по возрастанию "плотности".

    Returns:
        list: Список строк, представляющих ASCII-арт.
    """
    global background_image_cache
    if not os.path.exists(image_path):
        print(f"Предупреждение: Изображение не найдено: {image_path}")
        return []

    # Проверяем кэш
    cache_key = f"{image_path}-{width_in_chars}-{height_in_chars}"
    if cache_key in background_image_cache:
        cached_entry = background_image_cache[cache_key]
        if cached_entry['path'] == image_path:
            return cached_entry['lines']

    try:
        # Открываем изображение
        img = Image.open(image_path)

        # Вычисляем размеры пикселей для масштабирования
        # Соотношение сторон изображения
        img_aspect = img.width / img.height
        # Соотношение сторон консоли (приблизительно)
        # Учитываем, что символы в консоли обычно выше, чем шире (примерно 1:2)
        console_aspect = width_in_chars / height_in_chars
        console_aspect_adjusted = console_aspect / 2 # Приблизительная ширина символа к высоте

        # Масштабируем, чтобы изображение вписалось, возможно с обрезкой
        if img_aspect > console_aspect_adjusted:
            # Изображение шире, чем консоль -> обрезаем по высоте
            new_height = img.height
            new_width = int(img.height * console_aspect_adjusted)
        else:
            # Изображение уже или равно консоли -> обрезаем по ширине
            new_width = img.width
            new_height = int(img.width / console_aspect_adjusted)

        # Обрезаем изображение по центру
        left = (img.width - new_width) // 2
        top = (img.height - new_height) // 2
        right = left + new_width
        bottom = top + new_height
        img_cropped = img.crop((left, top, right, bottom))

        # Изменяем размер до нужного количества символов
        # Умножаем высоту на 2, чтобы компенсировать высокие символы в консоли
        target_size = (width_in_chars, height_in_chars)
        img_resized = img_cropped.resize(target_size)

        # Конвертируем в оттенки серого
        img_gray = img_resized.convert('L')

        # Преобразуем в ASCII
        pixels = img_gray.getdata()
        ascii_chars = []
        for i, pixel_value in enumerate(pixels):
            # Находим соответствующий символ плотности
            # pixel_value от 0 (черный) до 255 (белый)
            # char_density индексируется от 0 до len(char_density)-1
            char_index = int((pixel_value / max(pixels)) * (len(char_density) - 1))
            # Ограничиваем индекс
            char_index = min(char_index, len(char_density) - 1)
            ascii_chars.append(char_density[char_index])
            
            # Начинаем новую строку каждые width_in_chars символов
            if (i + 1) % width_in_chars == 0:
                ascii_chars.append('\n')

        # Объединяем символы в строки
        ascii_art = "".join(ascii_chars)
        ascii_lines = ascii_art.split('\n')
        # Убираем последнюю пустую строку, если она есть
        if ascii_lines and ascii_lines[-1] == '':
            ascii_lines.pop()

        # Сохраняем в кэш
        background_image_cache[cache_key] = {'path': image_path, 'lines': ascii_lines}
        with open("ascii_cache.json", "w") as f:
            json.dump(background_image_cache, f, indent=4)

        return ascii_lines

    except Exception as e:
        print(f"Ошибка при обработке изображения {image_path}: {e}")
        return []

def render_desktop(workspace_win, redraw=False):
    """Отображает рабочий стол с иконками"""
    global hovered_desktop_item, current_bg_image
    
    max_y, max_x = workspace_win.getmaxyx()
    
    try:
        workspace_win.clear()

        if current_bg_image is not None:
            ascii_lines = image_to_ascii(current_bg_image, max_x, max_y)
            for y, line in enumerate(ascii_lines):
                if y >= max_y:
                    break
                # Обрезаем строку до ширины окна
                truncated_line = line[:max_x]
                try:
                    workspace_win.addstr(y, 0, truncated_line)
                except curses.error:
                    # Игнорируем ошибки, если строка выходит за пределы
                    pass
        
        # Отображаем иконки
        for (grid_y, grid_x), item_index in desktop_grid.items():
            if item_index < len(desktop_items):
                item = desktop_items[item_index]
                screen_y = grid_y * 7
                screen_x = grid_x * 9
                if screen_y + 7 < max_y and screen_x + 9 < max_x:
                    # Определяем атрибут: выделение + подсветка избранного
                    if item_index == hovered_desktop_item:
                        attr = curses.A_REVERSE
                    elif item.get('in_favorite', False) and item["name"]!="..":
                        # Подсветка избранного — например, жёлтый текст на чёрном
                        attr = curses.color_pair(3)  # yellow fg, default bg
                    else:
                        attr = curses.A_NORMAL
        
                    for i in range(7):
                        if i < 5:
                            line = item['icon']['graphic'][i]
                            workspace_win.addstr(screen_y + i, screen_x, line[:9], attr)
                        else:
                            label_line = item['icon']['label_lines'][i-5] if i-5 < len(item['icon']['label_lines']) else ''
                            centered_label = f" {label_line.center(7)[:7]} "
                            workspace_win.addstr(screen_y + i, screen_x, centered_label, attr)
                    
    except curses.error:
        pass

def handle_desktop_mouse(mouse_event, workspace_win):
    """Обработка мыши на рабочем столе"""
    global hovered_desktop_item, current_tab, current_directory
    
    _, x, y, _, bstate = mouse_event
    
    # Определяем, над какой иконкой мышь
    grid_x = x // 9  # Теперь 9 символов на иконку
    grid_y = y // 7   # 7 строк на иконку
    #hovered_desktop_item = -1
    
    if (grid_y, grid_x) in desktop_grid:
        item_index = desktop_grid[(grid_y, grid_x)]
        if item_index < len(desktop_items):
            hovered_desktop_item = item_index
    
    # Обработка клика
    if bstate & curses.BUTTON1_CLICKED and hovered_desktop_item >= 0:
        item = desktop_items[hovered_desktop_item]
        
        if item['type'] == 'folder':
            if item['name'] == "..":
                if navigation_stack:
                    desktop_mode, current_directory = navigation_stack.pop()
                else:
                    desktop_mode = "normal"
                    current_directory = "."
            else:
                # Сохраняем текущее состояние
                navigation_stack.append((desktop_mode, current_directory))
                desktop_mode = "normal"
                current_directory = item['path']
            scan_desktop_items(workspace_win)
            hovered_desktop_item = -1
            return True
            
        elif item['type'] == 'executable' and item['name'].endswith('.sh'):
            # Запускаем .sh файл во вкладке
            full_path = os.path.join(current_directory, item['name']) if current_directory != "." else item['name']
            command = f"bash {full_path}"
            idx = create_new_tab()
            switch_to_tab(idx)
            run_command_in_pty(command, workspace_win, idx)
            force_render = 3
        else:
            # Открываем остальные файлы через micro
            full_path = os.path.join(current_directory, item['name']) if current_directory != "." else item['name']
            command = f"nano {full_path}"
            idx = create_new_tab()
            switch_to_tab(idx)
            run_command_in_pty(command, workspace_win, idx)
            force_render = 3
        
        #hovered_desktop_item = -1
        return True
        
    return False

def main(stdscr):
    global prefix_active, current_tab, input_buffer, force_render, mouse_enabled, hovered_menu_item, hovered_tab, iterations

    curses.curs_set(0)
    stdscr.clear()
    
    # Включаем raw mode для передачи всех клавиш
    curses.raw()
    # Включаем keypad mode для правильной обработки функциональных клавиш
    stdscr.keypad(True)
    
    # Включаем поддержку мыши
    if mouse_enabled:
        curses.mousemask(curses.ALL_MOUSE_EVENTS | curses.REPORT_MOUSE_POSITION)
        # Отправляем escape-последовательность для включения мыши в xterm
        print('\033[?1003h')  # Включить отслеживание всех движений мыши
        sys.stdout.flush()
    
    if curses.has_colors():
        curses.start_color()
        curses.use_default_colors()
        try:
            for fg in range(16):
                for bg in range(16):
                    pair_num = fg * 16 + bg + 1
                    try:
                        curses.init_pair(pair_num, fg, bg)
                    except curses.error:
                        pass
        except:
            for fg in range(8):
                for bg in range(8):
                    pair_num = fg * 8 + bg + 1
                    try:
                        curses.init_pair(pair_num, fg, bg)
                    except curses.error:
                        pass
        

    # Отключаем Ctrl+C для всего приложения
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    signal.signal(signal.SIGWINCH, handle_sigwinch)

    # Инициализация первой вкладки (главного экрана)
    if not tabs:
        create_main_tab()

    while True:
        max_y, max_x = stdscr.getmaxyx()
        if max_y < 2:
            stdscr.addstr(0, 0, "Окно слишком мало!")
            stdscr.refresh()
            stdscr.getch()
            continue

        # Пересоздаём окна при изменении размера
        workspace_win = curses.newwin(max_y - 1, max_x, 0, 0)
        taskbar_win = curses.newwin(1, max_x, max_y - 1, 0)
        
        # Включаем raw mode и keypad mode для рабочего окна
        workspace_win.keypad(True)

        # Основной цикл обработки
        while True:
            iterations+=1
            # Обновляем таскбар
            update_taskbar_for_tabs(taskbar_win)
            
            # Отображаем текущую вкладку
            render_current_tab(workspace_win, tabs[current_tab])

            # Если текущая вкладка - главная, показываем приглашение и меню
            if current_tab == 0 and tabs[0]['cmd'] is None:
                # Инициализируем рабочий стол при первом входе
                if not desktop_items:
                    scan_desktop_items(workspace_win)
                
                # Отображаем рабочий стол
                if iterations % 5 == 0 or 1:
                    render_desktop(workspace_win)
                    workspace_win.refresh()

            # Ждём ввод с таймаутом
            workspace_win.timeout(50)  # Уменьшаем таймаут для более отзывчивости
            ch = workspace_win.getch()
            global DEBUG_LAST_KEY_CODE
            DEBUG_LAST_KEY_CODE = ch
            if ch == -1:
                continue

            # Обработка событий мыши
            if ch == curses.KEY_MOUSE:
                try:
                    mouse_event = curses.getmouse()
                    if mouse_event:
                        handle_mouse_event(mouse_event, workspace_win, taskbar_win, max_y, max_x)
                except curses.error:
                    pass
                continue

            # Префикс Ctrl+B
            if ch == 2:  
                prefix_active = True
                continue

            # Обработка команд префикса
            if prefix_active:
                act_code = handle_prefix_char(ch, workspace_win, taskbar_win)
                if act_code == 2:
                    break  # выходим из внутреннего цикла — перерисовываем
                elif act_code == 3:
                    force_render = 3
                    #render_current_tab(workspace_win, tabs[current_tab])
                    break
                else:
                    prefix_active = False
                    continue
                    
            # Обработка ввода для главной вкладки
            if current_tab == 0 and tabs[0]['cmd'] is None:
                handle_main_input(ch, workspace_win)
            else:
                # Отправка ввода в активную вкладку
                try:
                    send_key_to_pty(ch, current_tab)
                except (OSError, ProcessLookupError):
                    # Вкладка закрыта, переключаемся на главную
                    close_tab(current_tab)
                    switch_to_tab(0)

def handle_mouse_event(mouse_event, workspace_win, tab_win, max_y, max_x):
    """Обработка событий мыши"""
    global current_tab, hovered_menu_item, hovered_tab
    
    _, x, y, _, bstate = mouse_event
    
    # Если кликнули на таскбар (последняя строка экрана)
    if y == max_y - 1:  # Таскбар находится на последней строке
        old_hovered = hovered_tab
        
        # Определяем, над какой вкладкой находится курсор
        tab_positions = calculate_tab_positions(max_x)[0]
        hovered_tab = -10
        for i, (start_x, end_x) in enumerate(tab_positions[prefix_items.__len__():]):
            if start_x <= x <= end_x and i < len(tabs):
                hovered_tab = i
                break
        
        # Если кликнули левой кнопкой мыши
        if bstate & curses.BUTTON1_CLICKED and hovered_tab >= 0:
            switch_to_tab(hovered_tab)
            hovered_tab = -10  # Сбрасываем hover после клика
            return
        elif bstate & curses.BUTTON1_CLICKED:
            for i, (start_x, end_x) in enumerate(tab_positions):
                if start_x <= x <= end_x:
                    if i < len(prefix_items): 
                        prefix_items[i]["click"](workspace_win, tab_win)
                    elif i > len(prefix_items)+len(tabs)-1:
                        postfix_items[::-1][i-(len(prefix_items)+len(tabs))]["click"](workspace_win, tab_win)
                
        return
    else:
        hovered_tab = -10
    
    # Если движение мыши или клик на главной вкладке
    if current_tab == 0 and tabs[0]['cmd'] is None:
        # Обработка мыши на рабочем столе
        if handle_desktop_mouse(mouse_event, workspace_win):
            # Если было взаимодействие с рабочим столом, перерисовываем
            workspace_win.clear()
            render_desktop(workspace_win)
            workspace_win.refresh()
    else:
        # Отправляем события мыши в активную вкладку (если программа поддерживает мышь)
        if should_enable_mouse_for_tab(current_tab):
            send_mouse_event_to_pty(mouse_event, current_tab)

def should_enable_mouse_for_tab(tab_index):
    """Проверяет, следует ли включить поддержку мыши для данной вкладки"""
    if tab_index >= len(tabs):
        return False
    
    tab = tabs[tab_index]
    if not tab['cmd']:
        return False
    
    cmd = tab['cmd'].strip()
    
    # Проверяем черный список
    for blacklisted in mouse_blacklist:
        if cmd.startswith(blacklisted):
            return False
    
    # Проверяем белый список
    for whitelisted in mouse_whitelist:
        if cmd.startswith(whitelisted):
            return True
    
    # Для остальных программ - включаем мышь по умолчанию
    # (можно изменить на False, если хотите более строгую политику)
    return True

def send_mouse_event_to_pty(mouse_event, tab_index):
    """Отправка событий мыши в PTY для поддержки мыши в запущенных программах"""
    tab = tabs[tab_index]
    try:
        # Извлекаем информацию о событии мыши
        _, x, y, _, bstate = mouse_event
        # Преобразуем координаты для терминала (1-based)
        x += 1
        y += 1
        # Определяем тип события мыши
        mouse_code = None
        suffix = 'M' # По умолчанию 'M' для нажатий/движений

        # --- НОВАЯ ЛОГИКА: Обработка событий в приоритетном порядке ---
        # Сначала обрабатываем события, которые требуют отправки как нажатие + отпускание
        if bstate & curses.BUTTON1_CLICKED:
            # Обработка клика: нажатие + отпускание
            mouse_code_press = 0  # Левая кнопка нажата
            mouse_escape_press = f"\033[<{mouse_code_press};{x};{y}M"
            os.write(tab['master_fd'], mouse_escape_press.encode('utf-8'))

            # Отправляем отпускание
            time.sleep(0.01)  # Небольшая задержка
            mouse_code_release = 3  # Левая кнопка отпущена
            mouse_escape_release = f"\033[<{mouse_code_release};{x};{y}m"
            os.write(tab['master_fd'], mouse_escape_release.encode('utf-8'))
            return # Завершаем обработку этого события
        elif bstate & curses.BUTTON2_CLICKED:
            # Обработка клика: нажатие + отпускание
            mouse_code_press = 1  # Средняя кнопка нажата
            mouse_escape_press = f"\033[<{mouse_code_press};{x};{y}M"
            os.write(tab['master_fd'], mouse_escape_press.encode('utf-8'))

            # Отправляем отпускание
            time.sleep(0.01)
            mouse_code_release = 4  # Средняя кнопка отпущена
            mouse_escape_release = f"\033[<{mouse_code_release};{x};{y}m"
            os.write(tab['master_fd'], mouse_escape_release.encode('utf-8'))
            return # Завершаем обработку этого события
        elif bstate & curses.BUTTON3_CLICKED:
            # Обработка клика: нажатие + отпускание
            mouse_code_press = 2  # Правая кнопка нажата
            mouse_escape_press = f"\033[<{mouse_code_press};{x};{y}M"
            os.write(tab['master_fd'], mouse_escape_press.encode('utf-8'))

            # Отправляем отпускание
            time.sleep(0.01)
            mouse_code_release = 5  # Правая кнопка отпущена
            mouse_escape_release = f"\033[<{mouse_code_release};{x};{y}m"
            os.write(tab['master_fd'], mouse_escape_release.encode('utf-8'))
            return # Завершаем обработку этого события

        # Теперь обрабатываем отдельные нажатия и отпускания
        # (Эти события могут не приходить, если curses интерпретирует как CLICKED)
        # Но если они приходят, обрабатываем их.
        elif bstate & curses.BUTTON1_PRESSED:
            mouse_code = 0  # Левая кнопка нажата
        elif bstate & curses.BUTTON1_RELEASED:
            mouse_code = 3  # Левая кнопка отпущена
            suffix = 'm' # Используем 'm' для отпускания
        elif bstate & curses.BUTTON2_PRESSED:
            mouse_code = 1  # Средняя кнопка нажата
        elif bstate & curses.BUTTON2_RELEASED:
            mouse_code = 4  # Средняя кнопка отпущена
            suffix = 'm'
        elif bstate & curses.BUTTON3_PRESSED:
            mouse_code = 2  # Правая кнопка нажата
        elif bstate & curses.BUTTON3_RELEASED:
            mouse_code = 5  # Правая кнопка отпущена
            suffix = 'm'
        elif bstate & curses.BUTTON4_PRESSED:
            mouse_code = 64  # Колесо мыши вверх
        elif bstate & curses.BUTTON5_PRESSED:
            mouse_code = 65  # Колесо мыши вниз
        else:
            if bstate & 32: # Движение с зажатой кнопкой (тип кнопки неизвестен)
                mouse_code = 32
            elif bstate == 0: # Просто движение
                pass # Или просто не отправляем код, и ниже mouse_code останется None
            else:
                pass # Или обработать по необходимости

        # Если mouse_code не был установлен, не отправляем ничего
        if mouse_code is None:
            return

        # Добавляем флаги модификаторов
        # Это важно для всех событий
        if bstate & curses.BUTTON_SHIFT:
            mouse_code |= 4
        if bstate & curses.BUTTON_ALT: # curses.BUTTON_ALT может не работать как ожидается
            mouse_code |= 8
        if bstate & curses.BUTTON_CTRL: # curses.BUTTON_CTRL может не работать как ожидается
            mouse_code |= 16

        # Формируем escape-последовательность в формате SGR
        mouse_escape = f"\033[<{mouse_code};{x};{y}{suffix}"
        os.write(tab['master_fd'], mouse_escape.encode('utf-8'))

    except (OSError, KeyError):
        pass # Или логируйте ошибку

def calculate_tab_positions(max_x):
    """Вычисляет позиции вкладок для определения наведения мыши"""
    if not tabs:
        return []
        
    prefix_place=sum(it["str"]().__len__() for it in prefix_items)
    postfix_place=sum(it["str"]().__len__() for it in postfix_items)
    
    # Рассчитываем позиции каждой вкладки
    tab_strings = [it["str"]() for it in prefix_items]
    total_width = prefix_place+postfix_place
    positions = []

    
    for i, tab in enumerate(tabs):
        label = f"[{i}:{tab['name']}]"
        if i == current_tab:
            label = f"▶ {label} ◀"
        tab_strings.append(label)
        total_width += len(label) 
    
    # Если все вкладки помещаются
    if total_width <= max_x:
        current_x = 0
        for label in tab_strings:
            positions.append((current_x, current_x + len(label) - 1))
            current_x += len(label)
        tab_strings+=[it["str"]() for it in postfix_items[::-1]]
        current_x=max_x-2
        for item in postfix_items[::-1]:
            positions.append((current_x-len(item["str"]())+1, current_x))
            current_x-= len(item["str"]())
    else:
        # Если не помещаются, распределяем равномерно
        tab_width = max_x // len(tabs)
        for i in range(len(tabs)):
            start_x = i * tab_width
            end_x = start_x + tab_width - 1
            positions.append((start_x, end_x))
    
    return positions, tab_strings

def send_key_to_pty(ch, tab_index):
    """Отправка клавиши в PTY с правильной обработкой специальных клавиш"""
    tab = tabs[tab_index]
    
    # Словарь для преобразования кодов клавиш в escape-последовательности
    key_map = {
        curses.KEY_UP: b'\x1b[A',
        curses.KEY_DOWN: b'\x1b[B',
        curses.KEY_RIGHT: b'\x1b[C',
        curses.KEY_LEFT: b'\x1b[D',
        curses.KEY_HOME: b'\x1b[H',
        curses.KEY_END: b'\x1b[F',
        curses.KEY_BACKSPACE: b'\x7f',  # DEL
        curses.KEY_DC: b'\x1b[3~',      # Delete
        curses.KEY_IC: b'\x1b[2~',      # Insert
        curses.KEY_NPAGE: b'\x1b[6~',   # Page Down
        curses.KEY_PPAGE: b'\x1b[5~',   # Page Up
        curses.KEY_F1: b'\x1bOP',
        curses.KEY_F2: b'\x1bOQ',
        curses.KEY_F3: b'\x1bOR',
        curses.KEY_F4: b'\x1bOS',
        curses.KEY_F5: b'\x1b[15~',
        curses.KEY_F6: b'\x1b[17~',
        curses.KEY_F7: b'\x1b[18~',
        curses.KEY_F8: b'\x1b[19~',
        curses.KEY_F9: b'\x1b[20~',
        curses.KEY_F10: b'\x1b[21~',
        curses.KEY_F11: b'\x1b[23~',
        curses.KEY_F12: b'\x1b[24~',
        curses.KEY_BTAB: b'\x1b[Z',     # Shift+Tab
        curses.KEY_SLEFT: b'\x1b[1;2D',  # Shift + Left Arrow
        curses.KEY_SRIGHT: b'\x1b[1;2C', # Shift + Right Arrow
        337: b'\x1b[1;2A',    # Shift + Up Arrow
        336: b'\x1b[1;2B',    # SDOWN
    }
    
    # Обработка специальных случаев
    if ch in key_map:
        os.write(tab['master_fd'], key_map[ch])
    elif ch == 10 or ch == 13:  # Enter
        os.write(tab['master_fd'], b'\r')  # Используем \r вместо \n
    elif ch == 9:  # Tab
        os.write(tab['master_fd'], b'\t')
    elif ch == 27:  # Escape
        os.write(tab['master_fd'], b'\x1b')
    elif ch == 127:  # Delete/Backspace
        os.write(tab['master_fd'], b'\x7f')
    elif 32 <= ch <= 126 or ch >= 160:  # Печатаемые символы
        os.write(tab['master_fd'], bytes([ch]))
    elif ch < 32:  # Ctrl+буква (коды 1-26 для a-z)
        # Передаем Ctrl+сочетания напрямую
        os.write(tab['master_fd'], bytes([ch]))
    elif 0<=ch<256:
        # Передаем остальные символы как есть
        os.write(tab['master_fd'], bytes([ch]))

def handle_main_input(ch, workspace_win):
    """Обработка ввода на главном экране"""
    global input_buffer, current_tab, hovered_desktop_item, current_directory, desktop_items, current_bg_image, desktop_mode, current_directory, navigation_stack
    
    # Обработка клавиш
    if ch in (curses.KEY_ENTER, 10, 13):
        # Enter - выполнить команду
        if hovered_desktop_item!=-1:
            item = desktop_items[hovered_desktop_item]
            if item['type'] == 'folder':
                if item['name'] == "..":
                    if navigation_stack:
                        desktop_mode, current_directory = navigation_stack.pop()
                    else:
                        desktop_mode = "normal"
                        current_directory = "."
                else:
                    # Сохраняем текущее состояние
                    navigation_stack.append((desktop_mode, current_directory))
                    desktop_mode = "normal"
                    current_directory = item['path']
                scan_desktop_items(workspace_win)
                hovered_desktop_item = -1
                return True
                    
            elif item['type'] == 'executable' and item['name'].endswith('.sh'):
                # Запускаем .sh файл во вкладке
                full_path = os.path.join(current_directory, item['name']) if current_directory != "." else item['name']
                command = f"bash {full_path}"
                idx = create_new_tab()
                switch_to_tab(idx)
                run_command_in_pty(command, workspace_win, idx)
                force_render = 3
            else:
                # Открываем остальные файлы через micro
                full_path = os.path.join(current_directory, item['name']) if current_directory != "." else item['name']
                command = f"nano {full_path}"
                idx = create_new_tab()
                switch_to_tab(idx)
                run_command_in_pty(command, workspace_win, idx)
                force_render = 3
            
            hovered_desktop_item = -1
        
            
    elif ch in (curses.KEY_UP, curses.KEY_DOWN, curses.KEY_LEFT, curses.KEY_RIGHT):
        if not desktop_items:
            hovered_desktop_item = -1
        else:
            max_y, max_x = workspace_win.getmaxyx()
            cols = max_x // 9
            rows = max_y // 7

            # Обратное отображение: item_index → (y, x)
            reverse_grid = {idx: pos for pos, idx in desktop_grid.items()}

            # Текущая позиция
            if hovered_desktop_item in reverse_grid:
                cur_y, cur_x = reverse_grid[hovered_desktop_item]
            else:
                # Если текущий элемент исчез — начинаем с первого
                cur_y, cur_x = next(iter(desktop_grid.keys()))
                hovered_desktop_item = desktop_grid[(cur_y, cur_x)]

            candidates = []

            if ch == curses.KEY_UP:
                # Ищем в том же столбце выше
                for y in range(cur_y - 1, -1, -1):
                    if (y, cur_x) in desktop_grid:
                        candidates.append((y, cur_x))
                        break
                # Если не нашли — ищем в соседних столбцах
                if not candidates:
                    for dx in range(1, max(cols, rows)):
                        for x in [cur_x - dx, cur_x + dx]:
                            if 0 <= x < cols:
                                for y in range(cur_y - 1, -1, -1):
                                    if (y, x) in desktop_grid:
                                        candidates.append((y, x))
                                        break
                            if candidates:
                                break
                        if candidates:
                            break

            elif ch == curses.KEY_DOWN:
                for y in range(cur_y + 1, rows):
                    if (y, cur_x) in desktop_grid:
                        candidates.append((y, cur_x))
                        break
                if not candidates:
                    for dx in range(1, max(cols, rows)):
                        for x in [cur_x - dx, cur_x + dx]:
                            if 0 <= x < cols:
                                for y in range(cur_y + 1, rows):
                                    if (y, x) in desktop_grid:
                                        candidates.append((y, x))
                                        break
                            if candidates:
                                break
                        if candidates:
                            break

            elif ch == curses.KEY_LEFT:
                for x in range(cur_x - 1, -1, -1):
                    if (cur_y, x) in desktop_grid:
                        candidates.append((cur_y, x))
                        break
                if not candidates:
                    for dy in range(1, max(cols, rows)):
                        for y in [cur_y - dy, cur_y + dy]:
                            if 0 <= y < rows:
                                for x in range(cur_x - 1, -1, -1):
                                    if (y, x) in desktop_grid:
                                        candidates.append((y, x))
                                        break
                            if candidates:
                                break
                        if candidates:
                            break

            elif ch == curses.KEY_RIGHT:
                for x in range(cur_x + 1, cols):
                    if (cur_y, x) in desktop_grid:
                        candidates.append((cur_y, x))
                        break
                if not candidates:
                    for dy in range(1, max(cols, rows)):
                        for y in [cur_y - dy, cur_y + dy]:
                            if 0 <= y < rows:
                                for x in range(cur_x + 1, cols):
                                    if (y, x) in desktop_grid:
                                        candidates.append((y, x))
                                        break
                            if candidates:
                                break
                        if candidates:
                            break

            # Выбираем ближайший кандидат (по умолчанию первый)
            if candidates:
                new_y, new_x = candidates[0]
                hovered_desktop_item = desktop_grid[(new_y, new_x)]
    # Обработка Shift+стрелок (коды: 337=↑, 336=↓, 393=←, 402=→)
    elif ch in (337, 336, 393, 402):  # Shift+стрелки
        if desktop_mode == "favorite" and hovered_desktop_item >= 0:
            # Найдём текущую позицию иконки в сетке
            reverse_grid = {idx: pos for pos, idx in desktop_grid.items()}
            if hovered_desktop_item not in reverse_grid:
                return
            cur_y, cur_x = reverse_grid[hovered_desktop_item]
    
            # Определяем смещение
            dx, dy = 0, 0
            if ch == 337: dy = -1   # ↑
            elif ch == 336: dy = +1 # ↓
            elif ch == 393: dx = -1 # ←
            elif ch == 402: dx = +1 # →
    
            new_x = cur_x + dx
            new_y = cur_y + dy
    
            max_y, max_x = workspace_win.getmaxyx()
            cols = max_x // 9
            rows = max_y // 7
    
            # Ограничиваем границами
            new_x = max(0, min(new_x, cols - 1))
            new_y = max(0, min(new_y, rows - 1))
    
            target_pos = (new_y, new_x)

            # --- Обновляем конфиг ---
            fav = config["paths"].get("favorite", {"files": [], "folders": []})

            def update_entry(path, new_x, new_y):
                for lst in [fav["folders"], fav["files"]]:
                    for entry in lst:
                        if entry[0] == path:
                            entry[1] = str(new_x)
                            entry[2] = str(new_y)
                            return
    
            # Обмен местами в сетке
            if target_pos in desktop_grid:
                # Есть иконка на целевой позиции — меняемся
                # Обновляем перемещаемую иконку
                moved_item = desktop_items[hovered_desktop_item]
                update_entry(moved_item['path'], new_x, new_y)
                update_entry(desktop_items[desktop_grid[target_pos]]['path'], cur_x, cur_y)
                other_idx = desktop_grid[target_pos]
                desktop_grid[target_pos] = hovered_desktop_item
                desktop_grid[(cur_y, cur_x)] = other_idx
            else:
                # Целевая ячейка свободна — просто перемещаем
                del desktop_grid[(cur_y, cur_x)]
                desktop_grid[target_pos] = hovered_desktop_item
                moved_item = desktop_items[hovered_desktop_item]
                update_entry(moved_item['path'], new_x, new_y)
    
            
    
            
    
            # Обновляем вытесненную (если есть)
            #if target_pos in desktop_grid and desktop_grid[target_pos] != hovered_desktop_item:
            #    other_item = desktop_items[desktop_grid[target_pos]]
                
    
            # Сохраняем
            with open("config.json", "w") as f:
                json.dump(config, f, indent=4)
    
            # Перерисовка
            scan_desktop_items(workspace_win)
            # Восстанавливаем выделение
            for idx, it in enumerate(desktop_items):
                if it['path'] == moved_item['path']:
                    hovered_desktop_item = idx
                    break
                                                    
    elif ch == curses.KEY_PPAGE:
        current_bg_image = bg_image_files[(bg_image_files.index(current_bg_image)+1)%len(bg_image_files)]
        render_desktop(workspace_win, True)
    elif ch == 1:  # Ctrl+A
        if hovered_desktop_item >= 0 and desktop_mode == "normal":
            item = desktop_items[hovered_desktop_item]
            fav = config["paths"].setdefault("favorite", {"files": [], "folders": []})
            full_path = item['path']
            if item['type'] == 'folder':
                if full_path not in fav["folders"]:
                    fav["folders"].append([full_path,"-","-"])
            else:
                if full_path not in fav["files"]:
                    fav["files"].append([full_path,"-","-"])
            with open("config.json", "w") as f:
                json.dump(config, f, indent=4)
            scan_desktop_items(workspace_win)
    elif ch == 4:  # Ctrl+D
        if hovered_desktop_item >= 0 and desktop_mode == "favorite":
            item = desktop_items[hovered_desktop_item]
            fav = config["paths"].get("favorite", {"files": [], "folders": []})
            full_path = item['path']
            if item['type'] == 'folder':    
                for p,x,y in fav["folders"]:
                    if full_path == p:
                        fav["folders"].remove([p,x,y])
            else:
                for p,x,y in fav["files"]:
                    if full_path == p:
                        fav["files"].remove([p,x,y])
            with open("config.json", "w") as f:
                json.dump(config, f, indent=4)
            scan_desktop_items(workspace_win)
            if hovered_desktop_item >= len(desktop_items):
                hovered_desktop_item -= 1
    elif ch == 8:  # Ctrl+H
        desktop_mode = "favorite"
        current_directory = "."
        navigation_stack = []
        scan_desktop_items(workspace_win)
        return
    else:
        pass


def handle_prefix_char(ch, win, twin):
    """Обработка команд после префикса Ctrl+B"""
    global prefix_active, current_tab, hovered_menu_item, hovered_tab
    if ch == ord('c') or ch == ord('C'):
        show_calendar_dialog(win, twin)
    elif ord('1') <= ch <= ord('9'):
        # Переключиться на вкладку 1-9
        idx = ch - ord('1')
        if idx < len(tabs):
            switch_to_tab(idx)
        prefix_active = False
        return 3 #break  # выходим, чтобы перерисовать
    elif ch == ord('w') or ch == ord('W'):
        # Закрыть текущую вкладку (кроме главной)
        if current_tab != 0:
            close_tab(current_tab)
        prefix_active = False
        return 2
    elif ch == ord('x') or ch == ord('X'):
        # Закрыть текущую вкладку принудительно (кроме главной)
        if current_tab != 0:
            force_close_tab(current_tab)
        prefix_active = False
        return 2
    elif ch == ord("z") or ch == ord("Z"):
        # Выход из приложения
        if mouse_enabled:
            print('\033[?1003l')  # Отключить отслеживание мыши
            sys.stdout.flush()
        sys.exit(0)		
    elif ch == ord("r") or ch == ord("R"):
        show_new_tab_dialog(win, twin)
    elif ch == ord("m") or ch == ord("M"):
        show_menu_dialog(win, twin)
    elif ch == ord('h') or ch == ord('H'):
        switch_to_tab(0)
        global desktop_mode, current_directory, navigation_stack
        desktop_mode = "favorite"
        current_directory = "."
        navigation_stack = []
        scan_desktop_items(win)
        prefix_active = False
        return 3
    else:
        # Любая другая клавиша — сбрасываем префикс
        prefix_active = False
        return 0

def show_prompt(win):
    """Отображение приглашения > на главном экране"""
    try:
        win.move(0, 0)
        win.clrtoeol()
        win.addstr(0, 0, "> ")
    except curses.error:
        pass

def update_taskbar_for_tabs(win):
    """Обновляет таскбар со списком вкладок"""
    global hovered_tab
    
    try:
        win.clear()
        max_y, max_x = win.getmaxyx()

        positions, tab_strings = calculate_tab_positions(max_x)

        # Если все вкладки помещаются
        total_width = sum(len(s) for s in tab_strings)
        if total_width <= max_x or 1:
            for i in range(tabs.__len__()+prefix_items.__len__()+postfix_items.__len__()):
                if i-len(prefix_items) == hovered_tab:
                    win.addstr(0, positions[i][0], tab_strings[i], curses.color_pair(7))
                else:
                    win.addstr(0, positions[i][0], tab_strings[i])
                
        else:
            # Если не помещаются, показываем только активную вкладку
            if tabs:
                active_label = tab_strings[current_tab]
                if len(active_label) <= max_x:
                    win.addstr(0, 0, active_label)
                else:
                    win.addstr(0, 0, active_label[:max_x])

        # Применяем инвертированный цвет ко всему таскбару по умолчанию
        win.bkgd(0, curses.A_REVERSE)

        win.refresh()
    except curses.error:
        pass

def set_pty_size(fd, rows, cols):
    winsize = struct.pack("HHHH", rows, cols, 0, 0)
    fcntl.ioctl(fd, termios.TIOCSWINSZ, winsize)

def handle_sigwinch(signum, frame):
    """Обработка изменения размера окна"""
    global hovered_tab
    
    # Сбрасываем hover при изменении размера
    hovered_tab = -1
    
    if not tabs:
        return
        
    # Обновляем размер для всех вкладок
    for i, tab in enumerate(tabs):
        if 'master_fd' in tab and tab['master_fd']:
            max_y, max_x = curses.LINES - 1, curses.COLS
            set_pty_size(tab['master_fd'], max_y - 1, max_x)
            if tab['screen']:
                tab['screen'].resize(max_y - 1, max_x)
                # Отправляем SIGWINCH в группу процессов
                if tab['pid']:
                    try:
                        os.killpg(os.getpgid(tab['pid']), signal.SIGWINCH)
                    except ProcessLookupError:
                        pass

def run_command_in_pty(command, workspace_win, tab_index, remember=True, dir_=None):
    """Запуск команды в PTY для указанной вкладки"""
    global force_render, config

    if dir_ == ".":
        dir_ = os.path.abspath(__file__)
        dir_ = dir_[:dir_.rfind("/")]
    
    if remember:
        for item in config["commands"]["recent"]:
            if item["name"]==command:
                config["commands"]["recent"].remove(item)
        config["commands"]["recent"].append({"name":command,"cmd":command if (dir_ is None or command.startswith("cd "+dir_)) else f"cd {dir_} && {command}"})
        with open("config.json", "w+") as f:
            json.dump(config,f,indent=4)
    
    tab = tabs[tab_index]
    rows, cols = curses.LINES - 1, curses.COLS

    # Сбрасываем экран
    tab['screen'].reset()
    tab['screen'].resize(rows - 1, cols)

    # Запускаем процесс
    try:
        env = os.environ.copy()
        env['TERM'] = 'xterm-256color'
        # Включаем поддержку мыши в терминале
        env['TERM_PROGRAM'] = 'xterm'
        # Добавляем переменную для программ, которые поддерживают мышь
        if should_enable_mouse_for_tab(tab_index):
            env['TERM'] = 'xterm-256color'
        # Используем preexec_fn для создания новой группы процессов
        p = subprocess.Popen(
            command if dir_ is None or command.startswith(dir_) else f"cd {dir_} && {command}",
            stdin=tab['slave_fd'],
            stdout=tab['slave_fd'],
            stderr=tab['slave_fd'],
            shell=True,
            start_new_session=True,  # Создаём новую сессию
            env=env
        )
        tab['pid'] = p.pid
        tab['cmd'] = command
        tab['name'] = command[:15].strip() if len(command) > 15 else command
    except Exception as e:
        tab['cmd'] = f"Ошибка: {e}"
        return

    # Создаём событие остановки для этой вкладки
    stop_event = threading.Event()
    tab_stop_events[tab_index] = stop_event

    # Запускаем фоновый поток для чтения
    thread = threading.Thread(target=read_from_pty, args=(tab, stop_event), daemon=True)
    thread.start()
    tab_threads[tab_index] = thread
    force_render = 2
    #render_current_tab(workspace_win, tabs[tab_index])

def read_from_pty(tab, stop_event):
    """Фоновый поток: читает из PTY и обновляет pyte-screen"""
    while not stop_event.is_set():
        try:
            r, _, _ = select.select([tab['master_fd']], [], [], 0.1)
            if tab['master_fd'] in r:
                output = os.read(tab['master_fd'], 4096)  # Увеличиваем буфер
                if output:
                    try:
                        # Пытаемся декодировать с разными кодировками
                        decoded = output.decode('utf-8')
                    except UnicodeDecodeError:
                        try:
                            decoded = output.decode('latin-1')
                        except UnicodeDecodeError:
                            decoded = output.decode('utf-8', errors='replace')
                    
                    tab['stream'].feed(decoded)
        except (OSError, UnicodeDecodeError):
            break
        except Exception:
            pass
        time.sleep(0.01)  # Уменьшаем задержку для лучшей отзывчивости

def create_main_tab():
    """Создаёт главную вкладку"""
    rows, cols = curses.LINES - 1, curses.COLS if curses.LINES > 1 and curses.COLS > 0 else (24, 80)
    
    screen = pyte.Screen(cols, rows - 1)
    stream = pyte.Stream(screen)
    
    tab = {
        'screen': screen,
        'stream': stream,
        'master_fd': None,
        'slave_fd': None,
        'cmd': None,      # Главная вкладка не имеет команды
        'name': "Главная",
        'active': True,
        'pid': None,
    }
    tabs.append(tab)
    return 0  # индекс главной вкладки

def create_new_tab():
    """Создаёт новую вкладку с пустым экраном"""
    rows, cols = curses.LINES - 1, curses.COLS
    
    screen = pyte.Screen(cols, rows - 1)
    stream = pyte.Stream(screen)
    screen.set_mode(pyte.modes.LNM, pyte.modes.DECTCEM)

    master_fd, slave_fd = pty.openpty()
    set_pty_size(slave_fd, rows - 1, cols)

    tab = {
        'screen': screen,
        'stream': stream,
        'master_fd': master_fd,
        'slave_fd': slave_fd,
        'cmd': None,      # Команда не запущена
        'name': f"Вкладка {len(tabs)}",
        'active': False,
        'pid': None,
    }
    tabs.append(tab)
    return len(tabs) - 1  # индекс новой вкладки

def close_tab(tab_index):
    """Пытается закрыть вкладку корректно"""
    global current_tab
    
    if tab_index == 0:
        return  # Нельзя закрыть главную вкладку
    
    if tab_index >= len(tabs):
        return
        
    tab = tabs[tab_index]
    
    # Пытаемся отправить SIGHUP для корректного завершения
    if tab['pid']:
        try:
            os.killpg(os.getpgid(tab['pid']), signal.SIGHUP)
        except ProcessLookupError:
            pass
    
    # Ждем немного для завершения
    time.sleep(0.1)
    
    # Проверяем, завершился ли процесс
    if tab['pid']:
        try:
            os.kill(tab['pid'], 0)  # Проверяем, существует ли процесс
            # Если процесс все еще существует, закрываем принудительно
            force_close_tab(tab_index)
        except ProcessLookupError:
            # Процесс уже завершен
            cleanup_tab(tab_index)
    else:
        cleanup_tab(tab_index)

def force_close_tab(tab_index):
    """Принудительно закрыть вкладку"""
    global current_tab
    
    if tab_index == 0:
        return  # Нельзя закрыть главную вкладку
    
    if tab_index >= len(tabs):
        return
        
    tab = tabs[tab_index]
    
    # Останавливаем поток
    if tab_index in tab_stop_events:
        tab_stop_events[tab_index].set()
        
    # Закрываем файловые дескрипторы
    if tab['master_fd']:
        try:
            os.close(tab['master_fd'])
        except OSError:
            pass
    if tab['slave_fd']:
        try:
            os.close(tab['slave_fd'])
        except OSError:
            pass
        
    # Убиваем процесс если он есть
    if tab['pid']:
        try:
            os.killpg(os.getpgid(tab['pid']), signal.SIGKILL)
        except ProcessLookupError:
            pass
    
    cleanup_tab(tab_index)

def cleanup_tab(tab_index):
    """Очистка ресурсов вкладки"""
    global current_tab, hovered_tab, force_render
    
    # Удаляем вкладку
    if tab_index < len(tabs):
        tabs.pop(tab_index)
    
    # Удаляем связанные данные
    if tab_index in tab_threads:
        del tab_threads[tab_index]
    if tab_index in tab_stop_events:
        del tab_stop_events[tab_index]
    
    # Корректируем индексы в словарях
    new_threads = {}
    new_events = {}
    for i, tab in enumerate(tabs):
        if i in tab_threads:
            new_threads[i] = tab_threads[i]
        if i in tab_stop_events:
            new_events[i] = tab_stop_events[i]
    
    tab_threads.clear()
    tab_stop_events.clear()
    tab_threads.update(new_threads)
    tab_stop_events.update(new_events)
    
    # Переключаемся на предыдущую вкладку
    if current_tab >= len(tabs):
        current_tab = len(tabs) - 1
    if current_tab < 0:
        current_tab = 0
    
    # Сбрасываем hover
    hovered_tab = -10
    force_render = 2

def switch_to_tab(index):
    """Переключение на вкладку"""
    global current_tab, hovered_tab, force_render
    if 0 <= index < len(tabs):
        current_tab = index
        hovered_tab = -10  # Сбрасываем hover при переключении
        force_render = 3

def render_current_tab(workspace_win, tab):
    """Отрисовывает содержимое активной вкладки"""
    global force_render, prev_curs_y
    
    if tab['cmd'] is None and current_tab == 0 and force_render:
        # Для главной вкладки без команды ничего не рисуем кроме приглашения
        workspace_win.clear()
        force_render-=1
        return
        
    if not tab['screen']:
        return
	
    rows, cols = workspace_win.getmaxyx()
    screen = tab['screen']
    #workspace_win.addstr(10,10,f'{screen.cursor.x} {screen.cursor.y}')
    prev_curs_y[1]=prev_curs_y[0]
    prev_curs_y[0]=screen.cursor.y
	
    if force_render:
        force_render-=1
        screen.dirty = set(range(curses.LINES-2)) 
    #if int(time.time())%10==0:
    #    screen.dirty = set(range(curses.LINES-2))
        
    screen.dirty.add(screen.cursor.y)
    screen.dirty.add(prev_curs_y[1])
    
    for y in sorted(screen.dirty):
        if y >= rows:
            continue
        workspace_win.move(y, 0)
        workspace_win.clrtoeol()
        for x in range(max(len(screen.buffer[y]), cols)):
            char_obj = screen.buffer[y][x]
            ch = char_obj.data
            fg = char_obj.fg
            bg = char_obj.bg
            attr = 0

            color_map = {
                'black': 0, 'red': 1, 'green': 2, 'yellow': 3, 'brown':3, 'brightbrown':11,
                'blue': 4, 'magenta': 5, 'cyan': 6, 'white': 7,
                'brightblack': 8, 'brightred': 9, 'brightgreen': 10, 'brightyellow': 11,
                'brightblue': 12, 'bfightmagenta': 13, 'brightmagenta': 13, 'brightcyan': 14, 'brightwhite': 15
            }

            fg_num = color_map.get(fg,-1)
            bg_num = color_map.get(bg,-1)

            if bg_num != -1 and 0 <= fg_num <= 15 and 0 <= bg_num <= 15:
                pair_num = fg_num * 16 + bg_num + 1
                attr |= curses.color_pair(pair_num)
            elif fg_num != -1 and 0 <= fg_num <= 15:
                attr |= curses.color_pair(fg_num * 16 + 1)
            elif bg_num != -1:
                attr |= curses.color_pair(15 * 16 + bg_num + 1)

            if char_obj.bold:
                attr |= curses.A_BOLD
            if char_obj.underscore:
                attr |= curses.A_UNDERLINE
            if char_obj.reverse or (x==screen.cursor.x and y==screen.cursor.y):
                attr |= curses.A_REVERSE

            try:
                workspace_win.addch(y, x, ch, attr)
            except curses.error:
                pass

    # Очищаем dirty ПОСЛЕ рендера
    screen.dirty.clear()

    # Вызываем doupdate()
    workspace_win.noutrefresh()


def show_new_tab_dialog(workspace_win, tab_win):
    """Показывает диалоговое окно для создания новой вкладки"""

    def find_command(source, command):
        for i, item in enumerate(source):
            if item["cmd"] == command:
                return i
        return -1
    
    max_y, max_x = workspace_win.getmaxyx()
    
    # Создаем оверлейное окно в центре экрана
    dialog_height = 3
    dialog_width = max(0,max_x-3)
    dialog_y = max_y - dialog_height -1
    dialog_x = 1
    
    # Создаем окно диалога
    dialog_win = curses.newwin(dialog_height, dialog_width, dialog_y, dialog_x)
    
    # Устанавливаем стили
    dialog_win.bkgd(' ', curses.A_REVERSE)
    dialog_win.box()
    
    # Показываем приглашение
    prompt_text = "Command: "
    dialog_win.addstr(1, 1, prompt_text)
    dialog_win.refresh()
    
    # Создаем окно для ввода (без рамки)
    input_win = curses.newwin(1, dialog_width - len(prompt_text) - 2, 
                             dialog_y + 1, dialog_x + len(prompt_text) + 1)
    input_win.bkgd(' ', curses.A_NORMAL)
    input_win.refresh()
    
    # Собираем ввод
    input_buffer = ""
    input_win.move(0, 0)
    
    while True:
        update_taskbar_for_tabs(tab_win)
        input_win.timeout(50)
        ch = input_win.getch()
        #DEBUG_LAST_KEY_CODE = find_command(last_commands,input_buffer)
        if ch in (curses.KEY_ENTER, 10, 13):  # Enter
            if input_buffer.strip():
                # Запускаем команду в новой вкладке
                command = input_buffer.strip()
                idx = create_new_tab()
                switch_to_tab(idx)
                run_command_in_pty(command, workspace_win, idx, dir_ = current_directory)
            break
            
        elif ch in (curses.KEY_BACKSPACE, 127, 8):  # Backspace
            if len(input_buffer) > 0:
                input_buffer = input_buffer[:-1]
                y, x = input_win.getyx()
                if x > 0:
                    input_win.move(0, x - 1)
                    input_win.delch()
                    input_win.insch(' ')
                    input_win.move(0, x - 1)
            
        elif ch == 65: #up UNUSED
            input_win.clear()
            if find_command(last_commands,input_buffer) != -1:
                input_buffer = last_commands[find_command(last_commands, input_buffer)]["cmd"]
            else:
                if config["commands"]["recent"]:
                    input_buffer = last_commands[0]["cmd"]  
            
            input_win.move(0,0)
            input_win.addstr(input_buffer)   
            continue  
             
        elif ch == 27:  # Escape
            break  # Отмена 

        elif 0 <= ch <= 128:  # Печатаемые символы
            input_buffer += chr(ch)
            input_win.addch(ch)
        else:
            pass
            
        input_win.refresh()
    
    # Удаляем диалоговые окна
    del dialog_win
    del input_win
    
    # Перерисовываем основной экран
    workspace_win.clear()
    workspace_win.refresh()
    global force_render
    force_render = 2

def show_menu_dialog(workspace_win, tab_win):
    """Показывает диалоговое меню с командами и файлами внизу в центре"""
    max_y, max_x = workspace_win.getmaxyx()
    
    # Захардкоженные пункты меню
    menu_items_col1 = config["commands"]["favorite"][:9]
    
    menu_items_col2 = config["commands"]["recent"][::-1][:9]

    shift_num_codes = { 
        33:1,
        64:2,
        35:3,
        36:4,
        37:5,
        94:6,
        38:7,
        42:8,
        40:9,
        41:0
    }
    shift_num_codes_reverse = { 
        1: 33,
        2: 64,
        3: 35,
        4: 36,
        5: 37,
        6: 94,
        7: 38,
        8: 42,
        9: 40,
        0: 41 
    }
    
    # Получаем информацию о пользователе
    import getpass
    username = getpass.getuser()
    is_root = os.geteuid() == 0
    user_info = f"{username}{' (root)' if is_root else ''}"
    
    # Вычисляем размеры окна
    max_item_len1 = max(len(item["name"]) for item in menu_items_col1) if menu_items_col1 else 10
    max_item_len2 = max(len(item["name"]) for item in menu_items_col2) if menu_items_col2 else 10
    col_width = max(15, max(max_item_len1, max_item_len2) + 5)  # +5 для отступов и номера
    dialog_width = col_width * 2 + 2  # +2 для разделителя
    dialog_height = max(len(menu_items_col1), len(menu_items_col2)) + 4  # +2 для рамки +1 для шапки +1 для поиска
    dialog_y = max_y - dialog_height - 1  # Прижимаем к низу
    dialog_x = (max_x - dialog_width) // 2  # Центрируем по горизонтали
    
    # Создаем окно диалога
    dialog_win = curses.newwin(dialog_height, dialog_width, dialog_y, dialog_x)
    
    # Устанавливаем стили
    dialog_win.bkgd(' ', curses.A_REVERSE)
    dialog_win.box()
    
    # Отображаем шапку с информацией о пользователе
    header = f" {user_info} "
    dialog_win.addstr(0, (dialog_width - len(header)) // 2, header, curses.A_BOLD)
    
    # Создаем окно для поиска
    search_height = 1
    search_width = dialog_width - 4  # -2 для отступов -2 для рамки
    search_y = dialog_height - 2  # Перед последней строкой рамки
    search_x = 2  # Отступ от левой рамки
    search_win = curses.newwin(search_height, search_width, dialog_y + search_y, dialog_x + search_x)
    search_win.bkgd(' ', curses.A_NORMAL)
    
    # Показываем разделитель между колонками
    for i in range(1, dialog_height - 2):  # -2 для шапки и поиска
        dialog_win.addstr(i, col_width, '│', curses.A_REVERSE)
    
    # Собираем ввод для поиска
    search_buffer = ""
    
    while True:
        # Очищаем область меню (между шапкой и полем поиска)
        for i in range(1, dialog_height - 2):
            dialog_win.move(i, 1)
            dialog_win.clrtoeol()
            dialog_win.box()
        
        # Фильтруем пункты по содержанию в поиске
        filtered_items_col1 = [item for item in menu_items_col1 if search_buffer.lower() in item["name"].lower() or search_buffer.lower() in item["cmd"].lower()]
        filtered_items_col2 = [item for item in menu_items_col2 if search_buffer.lower() in item["name"].lower() or search_buffer.lower() in item["cmd"].lower()]
        
        # Отображаем пункты меню
        max_items = max(len(filtered_items_col1), len(filtered_items_col2))
        for i in range(max_items):
            # Колонка 1
            if i < len(filtered_items_col1):
                item = filtered_items_col1[i]
                attr = curses.A_NORMAL
                dialog_win.addstr(i + 1, 1, (item["name"] + " " * col_width)[:col_width-4] + f" {i+1}", attr)
            
            # Колонка 2
            if i < len(filtered_items_col2):
                item = filtered_items_col2[i]
                attr = curses.A_NORMAL
                dialog_win.addstr(i + 1, col_width + 1, (item["name"] + " " * col_width)[:col_width-4] + f" {chr(shift_num_codes_reverse[i+1])}", attr)
        
        dialog_win.refresh()
        search_win.clear()
        search_win.addstr(0, 0, f"Search: {search_buffer}")
        search_win.refresh()

        search_win.timeout(50)
        update_taskbar_for_tabs(tab_win)
        ch = search_win.getch()
        
        if ch == 27:  # Escape - выйти из меню
            break
            
        elif ch in (curses.KEY_BACKSPACE, 127, 8):  # Backspace
            if len(search_buffer) > 0:
                search_buffer = search_buffer[:-1]

        elif 0 <= ch-ord('0')-1 <= 9:
            num = ch-ord('0')-1
            command = filtered_items_col1[num]["cmd"]
            idx = create_new_tab()
            switch_to_tab(idx)
            run_command_in_pty(command, workspace_win, idx)
            break

        elif ch in shift_num_codes.keys():
            num = shift_num_codes[ch]-1
            command = filtered_items_col2[num]["cmd"]
            idx = create_new_tab()
            switch_to_tab(idx)
            run_command_in_pty(command, workspace_win, idx)
            break
        
        elif 33 <= ch <= 126:  # Печатаемые символы
            search_buffer += chr(ch)
                
        elif ch == curses.KEY_RESIZE:
            break
    
    # Удаляем окна
    del dialog_win
    del search_win
    
    # Перерисовываем основной экран
    workspace_win.clear()
    workspace_win.refresh()
    global force_render
    force_render = 2
    
def show_calendar_dialog(workspace_win, tab_win):
    """Показывает диалоговое окно с календарем на текущий месяц"""
    max_y, max_x = workspace_win.getmaxyx()
    
    # Получаем текущую дату
    now = datetime.now()
    year = now.year
    month = now.month
    day = now.day
    
    # Генерируем календарь
    cal = calendar.month(year, month)
    cal_lines = list(cal.split('\n'))
    if not cal_lines[-1].strip(): cal_lines.remove(cal_lines[-1])
    cal_lines[-1]+='-'*(len(cal_lines[0])-len(cal_lines[-1])+2)
    
    # Убираем пустую последнюю строку если есть
    if cal_lines and not cal_lines[-1].strip():
        cal_lines.pop()
    
    # Вычисляем размеры окна
    dialog_width = max(25, max(len(line) for line in cal_lines) + 4)
    dialog_height = len(cal_lines) + 2
    dialog_y = max_y - dialog_height - 1
    dialog_x = max_x - dialog_width - 1
    
    # Создаем окно диалога
    dialog_win = curses.newwin(dialog_height, dialog_width, dialog_y, dialog_x)
    
    # Устанавливаем стили
    dialog_win.bkgd(' ', curses.A_REVERSE)
    dialog_win.box()
    
    # Отображаем календарь с выделением текущего дня
    for i, line in enumerate(cal_lines):
        if i < dialog_height - 2:  # Учитываем рамку
            dialog_win.addstr(i + 1, 1, ' ' * (dialog_width - 2))  # Очищаем строку
            
            # Если это строка с днями недели или днями месяца
            if i >= 2:  # Пропускаем заголовок и пустую строку
                # Проверяем, содержит ли строка текущий день
                modified_line = line
                day_str = str(day)
                
                # Если это строка с днями и содержит текущий день
                if day_str in line:
                    # Находим позицию текущего дня в строке
                    # Учитываем, что дни могут быть выровнены по разным позициям
                    parts = line.split()
                    new_parts = []
                    for part in parts:
                        if part.strip() == day_str:
                            # Выделяем текущий день
                            new_parts.append(f"[{day_str}]")
                        else:
                            new_parts.append(part)
                    modified_line = ' '.join(new_parts).replace(" [","[").replace("] ","]")
                
                # Центрируем текст
                padded_line = modified_line.center(dialog_width - 2)
                dialog_win.addstr(i + 1, 1, padded_line[:dialog_width-2].replace("-"," "))
                if "[" in padded_line:
                    dialog_win.addstr(i + 1, 1+padded_line.index("["), padded_line[padded_line.index("["):padded_line.index("]")+1].replace("[",">").replace("]","<").replace("-"," "), curses.color_pair(8))
            else:
                # Для заголовков просто центрируем
                padded_line = line.center(dialog_width - 2)
                dialog_win.addstr(i + 1, 1, padded_line[:dialog_width-2])
    
    dialog_win.refresh()
    
    # Ждем нажатия любой клавиши для закрытия
    while True:
        dialog_win.timeout(50)  # Блокирующий режим
        ch = dialog_win.getch()
        update_taskbar_for_tabs(tab_win)
        if ch != -1:
            break
        
    
    # Удаляем окно
    del dialog_win
    
    # Перерисовываем основной экран
    workspace_win.clear()
    workspace_win.refresh()
    
    global force_render
    force_render = 2



import traceback  # ← добавь в начало файла, если ещё не импортирован

if __name__ == "__main__":
    try:
        curses.wrapper(main)
    except KeyboardInterrupt:
        if mouse_enabled:
            print('\033[?1003l')  # Отключить отслеживание мыши
            sys.stdout.flush()
        print("Программа прервана пользователем")
    except Exception as e:
        if mouse_enabled:
            print('\033[?1003l')  # Отключить отслеживание мыши
            sys.stdout.flush()
        # Выводим полный стектрейс
        traceback.print_exc()
        print(f"Произошла ошибка: {e}")

