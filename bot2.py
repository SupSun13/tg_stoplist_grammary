import os
import logging
import asyncio
from aiogram import Bot, Dispatcher, types, F, Router
from aiogram.filters import Command
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from dotenv import load_dotenv
import fitz  # PyMuPDF
from natasha import NamesExtractor, MorphVocab
import pymorphy3
import re
from aiogram.types import CallbackQuery
import openai
from openai import OpenAI

# --- Загрузка переменных окружения ---
load_dotenv()

# --- Инициализация OpenAI клиента ---
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# --- Роль для проверки фамилий ---
role = '''
Ты — лингвистический ассистент, специализирующийся на анализе имен собственных. Твоя задача — анализировать заданный текст (контекст) и определять, является ли указанное в вопросе слово в этом контексте фамилией (именем собственным), а не прилагательным или другой частью речи.
# Инструкции
1.  **Входные данные:** Тебе будут предоставлены:
    *   `[Целевое ФИО]`: Полное ФИО человека для сравнения (например, "Иванов Иван Иванович").
    *   `[Слово]`: Слово, которое нужно проанализировать.
    *   `[Контекст]`: Предложение или текст для анализа.
2.  **Шаг 1: Определение фамилии**
    *   Внимательно изучи `[Контекст]`. Определи, используется ли `[Слово]` в данном контексте как фамилия (имя собственное) или как прилагательное/нарицательное существительное (имя нарицательное).
    *   *Пример: В контексте "Завод производит стальные трубы" слово "стальные" — прилагательное. В контексте "Инженер Стальной получил премию" слово "Стальной" — фамилия.*
3.  **Шаг 2: Сравнение с целевым ФИО (если это фамилия)**
    *   Если `[Слово]` является фамилией, извлеки из `[Целевое ФИО]` фамилию (первое слово) и сравни ее с `[Слово]`.
    *   Если фамилии совпадают, проверь, относится ли упоминание в контексте к тому же человеку, что и `[Целевое ФИО]`. Для этого ищи в контексте совпадения по имени, отчеству, должности, инициалам, роду или другим признакам. Если полное совпадение или явные указания на человека из `[Целевое ФИО]` есть — это он. Если нет — это однофамилец.
4.  **Формат ответа:** Ты должен строго выводить ответ в следующем формате, без лишних объяснений:
    *   `фамилия: да` или `фамилия: нет`
    *   Затем выведи:
        `совпадение: <результат>`
    Где `<результат>` может быть одним из четырёх вариантов:
        - `target_person` (это тот самый человек из целевого ФИО)
        - `однофамилец` (фамилия совпадает, но это другой человек)
        - `неизвестно` (фамилия совпадает, но в контексте недостаточно данных, чтобы определить, тот это человек или однофамилец)
        - '-' (если не фамилия)
# Примеры вывода
**Вход 1:**
Контекст: "На совещании выступил Петров Алексей."
Слово: Петров
Целевое ФИО: Петров Сергей Владимирович
**Ответ:**
фамилия: да
совпадение: однофамилец
**Вход 2:**
Контекст: "Мы купили новый великий автомобиль."
Слово: великий
Целевое ФИО: Великий Александр Петрович
**Ответ:**
фамилия: нет
совпадение: -
**Вход 3:**
Контекст: "Профессор А С Иванова опубликовала новую статью."
Слово: Иванова
Целевое ФИО: Иванова Анна Сергеевна
**Ответ:**
фамилия: да
совпадение: target_person //(Если предполагается, что контекст указывает на того же человека)
// или `неизвестно`, если нельзя точно установить, та ли это Анна Сергеевна.
**Вход 4:**
Контекст: "Нам нужны белые краски для ремонта."
Слово: белые
Целевое ФИО: Белая Инна Викторовна
**Ответ:**
фамилия: нет
совпадение: -
**Вход 5:**
ФИО: Марков Сергей Александрович
Найдено: 'Маркову'
Контекст: классическая теорема принадлежащая А А Маркову показывает что это определение корректно
**Ответ:**
фамилия: да
совпадение: однофамилец'''

# --- Загрузка списков ---
file = open('inoagents_050925.txt', 'r', encoding='utf-8')
stop_list = file.read().split(', ')
file.close() # Закрытие файла

file = open('ia_initials_050925.txt', 'r', encoding='utf-8')
initial_slist = file.read().split(', ')
file.close() # Закрытие файла

# --- Функции для работы с фамилиями (остаются без изменений) ---
def create_last_name_variations(last_name):
    """
    Создает возможные варианты склонения фамилии
    """
    morph = pymorphy3.MorphAnalyzer()
    variations = set()
    # Парсим фамилию
    parsed = morph.parse(last_name)[0]
    # Добавляем нормальную форму
    variations.add(parsed.normal_form.lower())
    # Генерируем варианты в разных падежах
    cases = ['nomn', 'gent', 'datv', 'accs', 'ablt', 'loct']
    for case in cases:
        try:
            inflected = parsed.inflect({case})
            if inflected:
                variations.add(inflected.word.lower())
        except:
            continue
    return variations

def universal_extract_last_name(full_name):
    """
    Универсальная функция для извлечения фамилии
    """
    if not full_name or not isinstance(full_name, str):
        return None
    # Удаляем никнеймы в кавычках
    cleaned_name = re.sub(r'"[^"]*"', '', full_name)
    # Удаляем лишние пробелы
    cleaned_name = re.sub(r'\s+', ' ', cleaned_name).strip()
    if not cleaned_name:
        return None
    # Разбиваем на слова
    words = cleaned_name.split()
    if not words:
        return None
    # Возвращаем первое слово
    return words[0]

def create_full_names_map(initial_slist):
    """
    Создает словарь для маппинга фамилий на полные ФИО
    """
    full_names_map = {}
    for full_name in initial_slist:
        surname = universal_extract_last_name(full_name)
        if surname:
            full_names_map[surname] = full_name
    return full_names_map

def find_full_name_in_text(text, target_variations, full_names_map):
    """
    Ищет фамилию в тексте и возвращает список найденных вхождений с контекстом
    Использует stop_list для поиска, но возвращает полные ФИО из initial_slist
    """
    if not text or not target_variations or not full_names_map:
        return []
    # Разбиваем текст на слова с сохранением позиций
    words = re.findall(r'\b[а-яёА-ЯЁ]+\b', text)
    lower_words = [word.lower() for word in words]
    results = []
    # Ищем совпадения
    for i, word in enumerate(lower_words):
        if word in target_variations:
            # Определяем границы контекста
            start_idx = max(0, i - 5)
            end_idx = min(len(words), i + 6)  # 5 слов после + само слово
            # Формируем контекст
            context = ' '.join(words[start_idx:end_idx])
            # Получаем полное ФИО из маппинга
            surname = target_variations[word]  # Фамилия из stop_list
            full_name = full_names_map.get(surname, surname)  # Полное ФИО или фамилия если нет маппинга
            results.append({
                'full_name': full_name,          # Полное ФИО из initial_slist
                'surname': surname,              # Фамилия из stop_list
                'matched_word': words[i],        # Найденное слово в тексте
                'context': context,
                'position': i,
                'original': full_name  # ДЛЯ ОБРАТНОЙ СОВМЕСТИМОСТИ
            })
    return results

def variations_by_name(target_last_names):
    all_variations = {}
    for last_name in target_last_names:
        variations = create_last_name_variations(last_name)
        for variation in variations:
            all_variations[variation] = last_name  # Сохраняем оригинальную фамилию
    return all_variations

# --- Настройка логирования и инициализация бота ---
logging.basicConfig(level=logging.INFO)
router = Router()
bot = Bot(token=os.getenv('BOT_TOKEN'))
dp = Dispatcher()
dp.include_router(router)

# Папка для сохранения файлов (создаем если не существует)
FILE_STORAGE = "received_files"
os.makedirs(FILE_STORAGE, exist_ok=True)

# --- Функции для работы с GPT ---
async def check_with_gpt(target_fio, word, context):
    """
    Проверяет через GPT, является ли слово фамилией и тем ли человеком
    """
    try:
        content = f"Целевое ФИО: {target_fio}\nСлово: {word}\nКонтекст: {context}"
        completion = client.chat.completions.create(
            model="gpt-5-mini", # Используем подходящую модель
            messages=[
                {"role": "system", "content": role},
                {"role": "user", "content": content}
            ],
            # reasoning_effort и verbosity могут быть не поддержаны всеми моделями
            # Убираем их или используем только если точно знаем, что модель поддерживает
        )
        response = completion.choices[0].message.content
        return parse_gpt_response(response)
    except Exception as e:
        logging.error(f"Ошибка при запросе к GPT (фамилии): {e}")
        return None

def parse_gpt_response(response):
    """
    Парсит ответ GPT в структурированный формат
    """
    result = {
        'is_surname': False,
        'match_type': '-',
        'raw_response': response
    }
    lines = response.strip().split('\n')
    for line in lines:
        if line.startswith('фамилия:'):
            result['is_surname'] = 'да' in line.lower()
        elif line.startswith('совпадение:'):
            match_part = line.split(':', 1)[1].strip()
            result['match_type'] = match_part
    return result

# --- Новый промт для проверки орфографии/грамматики ---
spelling_role_tex = '''
Ты — лингвистический эксперт по русскому языку. Проанализируй текст и найди все орфографические и грамматические ошибки. Текст может содержать элементы формата LaTeX, игнорируй команды LaTeX, фокусируясь на проверке русского языка в обычном тексте.

# Формат ответа:
Для каждой ошибки выведи строго в формате:
{тип_ошибки}|{контекст_слева}|{неправильное_слово}|{контекст_справа}|{исправленный_вариант}
Где:
- {тип_ошибки}: "орфография" или "грамматика"
- {контекст_слева}: 2-3 слова перед ошибкой
- {неправильное_слово}: слово с ошибкой
- {контекст_справа}: 2-3 слова после ошибки  
- {исправленный_вариант}: правильная версия

# Примеры:
орфография|вчера я купил|картофан|для салата|картофель
грамматика|он пришел|время|совещания|вовремя
орфография|это очень|интиресная|книга|интересная

Если ошибок нет, верни "нет_ошибок".
'''

# --- Функции для проверки орфографии/грамматики ---
async def check_spelling_grammar_text(text: str, batch_num: int = None) -> list:
    """Проверяет орфографию и грамматику в тексте (батче) через GPT"""
    batch_info = f" (Батч {batch_num})" if batch_num is not None else ""
    try:
        completion = client.chat.completions.create(
            model="gpt-5-nano", # Используем подходящую модель
            messages=[
                {"role": "system", "content": spelling_role_tex},
                {"role": "user", "content": f"Проверь текст{batch_info}:\n{text}"}
            ],
            # reasoning_effort и verbosity могут быть не поддержаны всеми моделями
        )
        response = completion.choices[0].message.content.strip()
        if response == "нет_ошибок" or not response:
            return []

        errors = []
        for line in response.split('\n'):
            if '|' in line:
                parts = line.split('|', 4)  # Разделяем на 5 частей
                if len(parts) == 5:
                    error_type, left_context, wrong_word, right_context, correction = parts
                    errors.append({
                        'batch': batch_num,
                        'type': error_type,
                        'left_context': left_context.strip(),
                        'wrong_word': wrong_word.strip(),
                        'right_context': right_context.strip(),
                        'correction': correction.strip(),
                        'full_context': f"{left_context} {wrong_word} {right_context}"
                    })
        return errors
    except Exception as e:
        logging.error(f"Ошибка GPT при проверке орфографии{batch_info}: {e}")
        return []

# --- Функция для создания батчей из .tex файла ---
def create_tex_batches(file_path: str, max_size: int = 5000) -> list:
    """Читает .tex файл и разбивает его на батчи."""
    batches = []
    current_batch = []
    current_size = 0

    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            for line in f:
                line_size = len(line.encode('utf-8')) # Считаем размер в байтах для точности, или len(line) для символов
                # Проверка на превышение размера батча
                if current_size + line_size > max_size and current_batch:
                    # Заканчиваем текущий батч и начинаем новый
                    batches.append(''.join(current_batch))
                    current_batch = [line]
                    current_size = line_size
                else:
                    # Добавляем строку в текущий батч
                    current_batch.append(line)
                    current_size += line_size

            # Добавляем последний незавершенный батч, если он не пуст
            if current_batch:
                batches.append(''.join(current_batch))

    except Exception as e:
        logging.error(f"Ошибка при создании батчей из {file_path}: {e}")
        return [] # Возвращаем пустой список в случае ошибки

    return batches

# --- Асинхронная функция для обработки .tex файлов ---
async def check_spelling_grammar_tex(file_path: str, message: types.Message):
    """Проверяет орфографию и грамматику в .tex файле по батчам."""
    try:
        await message.answer("🔍 Начинаю проверку орфографии и грамматики в .tex файле...")
        batches = create_tex_batches(file_path)
        if not batches:
             await message.answer("❌ Не удалось создать батчи из .tex файла.")
             return

        await message.answer(f"📄 Обнаружено батчей: {len(batches)}")

        all_errors = []
        for i, batch in enumerate(batches):
            # Пропускаем пустые батчи
            if not batch.strip():
                continue

            batch_errors = await check_spelling_grammar_text(batch, i+1)
            all_errors.extend(batch_errors)
            # Отправляем прогресс каждые 5 батчей
            if (i + 1) % 5 == 0 or (i + 1) == len(batches):
                await message.answer(f"⏳ Обработано {i + 1}/{len(batches)} батчей")

        # Отправляем результаты
        await send_spelling_results(message, all_errors, is_tex=True)
    except Exception as e:
        logging.error(f"Ошибка проверки орфографии .tex файла: {e}")
        await message.answer("❌ Ошибка при проверке орфографии .tex файла")

# --- Измененная функция для проверки PDF файлов ---
async def check_spelling_grammar_pdf(file_path: str, message: types.Message):
    """Проверяет орфографию и грамматику в PDF (постранично)."""
    try:
        doc = fitz.open(file_path)
        total_pages = doc.page_count
        await message.answer(f"📄 Обнаружено страниц: {total_pages}")
        await message.answer("🔍 Начинаю проверку орфографии и грамматики...")

        all_errors = []
        for page_num in range(total_pages):
            page = doc.load_page(page_num)
            text = page.get_text()
            if text.strip():
                # Передаем номер страницы как batch_num для унификации
                page_errors = await check_spelling_grammar_text(text, page_num + 1)
                all_errors.extend(page_errors)
                # Отправляем прогресс каждые 5 страниц
                if (page_num + 1) % 5 == 0 or (page_num + 1) == total_pages:
                    await message.answer(f"⏳ Обработано {page_num + 1}/{total_pages} страниц")

        doc.close()
        # Отправляем результаты
        await send_spelling_results(message, all_errors, is_tex=False)
    except Exception as e:
        logging.error(f"Ошибка проверки орфографии PDF: {e}")
        await message.answer("❌ Ошибка при проверке орфографии PDF")

# --- Универсальная функция для отправки результатов ---
async def send_spelling_results(message: types.Message, errors: list, is_tex: bool = False):
    """Отправляет результаты проверки орфографии."""
    if not errors:
        await message.answer("✅ Орфографических и грамматических ошибок не найдено!")
        return

    # Группируем ошибки по батчам/страницам
    errors_by_batch = {}
    for error in errors:
        batch = error['batch']
        if batch not in errors_by_batch:
            errors_by_batch[batch] = []
        errors_by_batch[batch].append(error)

    # Отправляем сводку
    file_type = ".tex" if is_tex else "PDF"
    await message.answer(f"📊 Найдено ошибок в {file_type} файле: {len(errors)}")

    # Отправляем ошибки по батчам/страницам
    for batch_num, batch_errors in errors_by_batch.items():
        batch_type = "Батч" if is_tex else "Страница"
        batch_msg = f"📄 {batch_type} {batch_num}:\n"
        for i, error in enumerate(batch_errors, 1):
            error_msg = (f"{i}. {error['type'].upper()}: {error['full_context']}\n"
                       f"   ❌ Неправильно: {error['wrong_word']}\n"
                       f"   ✅ Правильно: {error['correction']}\n")
            if len(batch_msg) + len(error_msg) > 4000:
                await message.answer(batch_msg)
                batch_msg = f"📄 {batch_type} {batch_num} (продолжение):\n"
            batch_msg += error_msg
        if batch_msg.strip():
            await message.answer(batch_msg)
            await asyncio.sleep(0.7) # Небольшая задержка между сообщениями

# --- Обработчики команд и сообщений ---
@dp.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer("Привет! Отправь мне PDF или .tex файл для обработки.")

async def ask_check_type(message: types.Message, file_path: str, is_tex: bool = False):
    """Спрашивает пользователя о типе проверки"""
    check_type = "tex" if is_tex else "pdf"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Проверить по стоп-листу", callback_data=f"check_stoplist:{file_path}:{check_type}"),
            InlineKeyboardButton(text="📝 Проверить орфографию и грамматику", callback_data=f"check_spelling:{file_path}:{check_type}")
        ]
    ])
    await message.answer("🔍 Выберите тип проверки:", reply_markup=keyboard)

@dp.message(F.document)
async def handle_document(message: Message):
    # Проверяем MIME-тип или расширение
    if message.document.mime_type == 'application/pdf':
        is_tex = False
    elif message.document.file_name.lower().endswith('.tex'):
        is_tex = True
    else:
         await message.answer("Пожалуйста, отправьте PDF или .tex файл!")
         return

    try:
        # Скачиваем файл
        file = await bot.get_file(message.document.file_id)
        downloaded = await bot.download_file(file.file_path)
        filename = message.document.file_name or f"document_{message.document.file_id}.{'tex' if is_tex else 'pdf'}"
        filepath = os.path.join(FILE_STORAGE, filename)
        with open(filepath, 'wb') as f:
            f.write(downloaded.getvalue())
        await message.answer(f"✅ Файл сохранен!")

        # Спрашиваем тип проверки, передавая информацию о типе файла
        await ask_check_type(message, filepath, is_tex)

    except Exception as e:
        logging.error(f"Ошибка при обработке файла: {e}")
        await message.answer(f"❌ Ошибка при обработке файла: {e}")

# --- Обработчики callback'ов ---
@router.callback_query(F.data.startswith("check_stoplist:"))
async def handle_check_stoplist(callback: CallbackQuery):
    """Обработчик проверки по стоп-листу"""
    try:
        await callback.answer("⏳ Начинаю проверку по стоп-листу...")
        data_parts = callback.data.split(":", 2)
        if len(data_parts) < 3:
             await callback.message.answer("❌ Некорректные данные для проверки по стоп-листу.")
             return

        file_path = data_parts[1]
        file_type = data_parts[2] # 'pdf' или 'tex'

        if file_type == 'pdf':
            # Ваш существующий код для PDF
            # (Здесь нужно адаптировать process_pdf_pages, если нужно, или оставить как есть)
            # Для примера, предположим, что process_pdf_pages существует
            await process_pdf_pages(file_path, callback.message)
        elif file_type == 'tex':
             await callback.message.answer("⚠️ Проверка по стоп-листу для .tex файлов пока не реализована или не применима.")
        else:
             await callback.message.answer("❌ Неизвестный тип файла для проверки по стоп-листу.")

    except Exception as e:
        logging.error(f"Ошибка в check_stoplist: {e}")
        await callback.message.answer("❌ Ошибка при проверке по стоп-листу")

@router.callback_query(F.data.startswith("check_spelling:"))
async def handle_check_spelling(callback: CallbackQuery):
    """Обработчик проверки орфографии и грамматики"""
    try:
        await callback.answer("⏳ Начинаю проверку орфографии...")
        data_parts = callback.data.split(":", 2)
        if len(data_parts) < 3:
             await callback.message.answer("❌ Некорректные данные для проверки орфографии.")
             return

        file_path = data_parts[1]
        file_type = data_parts[2] # 'pdf' или 'tex'

        if file_type == 'pdf':
            await check_spelling_grammar_pdf(file_path, callback.message)
        elif file_type == 'tex':
            await check_spelling_grammar_tex(file_path, callback.message)
        else:
             await callback.message.answer("❌ Неизвестный тип файла для проверки орфографии.")

    except Exception as e:
        logging.error(f"Ошибка в check_spelling: {e}")
        await callback.message.answer("❌ Ошибка при проверке орфографии")

# --- Основная функция постраничной обработки PDF (оставлена для совместимости с PDF) ---
# (Предполагается, что функция process_pdf_pages существует и реализована)
# ... (ваша существующая реализация process_pdf_pages, если она нужна для PDF) ...
# Пример заглушки, если нужно просто протестировать:
async def process_pdf_pages(file_path: str, message: types.Message):
    """Основная функция постраничной обработки (стоп-лист)"""
    try:
        stop_pages = {}
        verified_pages = {}  # Для проверенных результатов
        unknown_pages = {}   # Для неизвестных
        namesake_pages = {}  # Для однофамильцев
        
        # Создаем маппинг фамилий на полные ФИО
        full_names_map = create_full_names_map(initial_slist)
        
        # Создаем вариации только для фамилий из stop_list
        variations = variations_by_name(stop_list)

        doc = fitz.open(file_path)
        total_pages = doc.page_count
        
        await message.answer(f"📄 Обнаружено страниц: {total_pages}")
        await message.answer("🔍 Начинаю анализ с проверкой через GPT...")
        
        # Обрабатываем каждую страницу
        for page_num in range(total_pages):
            page = doc.load_page(page_num)
            text = page.get_text()
            
            # Ищем вхождения с контекстом
            occurrences = find_full_name_in_text(text, variations, full_names_map)
            
            if occurrences:
                stop_pages[page_num] = occurrences
                verified_occurrences = []
                unknown_occurrences = []
                namesake_occurrences = []
                
                # Проверяем каждое вхождение через GPT
                for occ in occurrences:
                    gpt_result = await check_with_gpt(
                        occ['full_name'], 
                        occ['matched_word'], 
                        occ['context']
                    )
                    
                    if gpt_result:
                        occ['gpt_result'] = gpt_result
                        
                        if gpt_result['match_type'] == 'target_person':
                            verified_occurrences.append(occ)
                        elif gpt_result['match_type'] == 'неизвестно':
                            unknown_occurrences.append(occ)
                        elif gpt_result['match_type'] == 'однофамилец':
                            namesake_occurrences.append(occ)
                
                # Сохраняем разделенные результаты
                if verified_occurrences:
                    verified_pages[page_num] = verified_occurrences
                if unknown_occurrences:
                    unknown_pages[page_num] = unknown_occurrences
                if namesake_occurrences:
                    namesake_pages[page_num] = namesake_occurrences
                
                print(f"Обработана страница {page_num}: {len(verified_occurrences)} подтвержденных")
        
        doc.close()
        
        # Формируем отчет
        total_verified = sum(len(occ) for occ in verified_pages.values())
        total_unknown = sum(len(occ) for occ in unknown_pages.values())
        total_namesake = sum(len(occ) for occ in namesake_pages.values())
        
        # Создаем клавиатуру для выбора
        keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
            [
                types.InlineKeyboardButton(text="✅ Подтвержденные", callback_data="show_verified"),
                types.InlineKeyboardButton(text="❓ Неизвестные", callback_data="show_unknown")
            ],
            [
                types.InlineKeyboardButton(text="👥 Однофамильцы", callback_data="show_namesake"),
                types.InlineKeyboardButton(text="📊 Все результаты", callback_data="show_all")
            ]
        ])
        
        report_msg = (
            f"✅ Анализ завершен!\n\n"
            f"📊 Результаты проверки GPT:\n"
            f"• ✅ Подтвержденных нарушений: {total_verified}\n"
            f"• ❓ Неизвестных: {total_unknown}\n"
            f"• 👥 Однофамильцев: {total_namesake}\n\n"
            f"Выберите, что показать:"
        )
        
        await message.answer(report_msg, reply_markup=keyboard)
        
        # Сохраняем результаты в глобальные переменные для callback
        global analysis_results
        analysis_results = {
            'verified': verified_pages,
            'unknown': unknown_pages,
            'namesake': namesake_pages,
            'all': stop_pages
        }
        
    except Exception as e:
        logging.error(f"Ошибка обработки: {e}")
        await message.answer("❌ Ошибка при анализе PDF")

# --- Глобальная переменная для хранения результатов (если используется) ---
analysis_results = {}

# --- Функции для отправки результатов (остаются без изменений, если не касаются .tex) ---
# ... (ваша существующая реализация send_results, safe_send_message и т.д., если они нужны для других частей) ...
async def safe_send_message(message: types.Message, text: str, delay=0.7, max_retries=3):
    """
    Безопасная отправка сообщения с повторными попытками и задержками
    """
    for attempt in range(max_retries):
        try:
            await message.answer(text)
            await asyncio.sleep(delay)
            return True
        except Exception as e:
            if "Too Many Requests" in str(e) or "429" in str(e):
                wait_time = 2 ** attempt  # Экспоненциальная backoff
                logging.warning(f"Too Many Requests. Жду {wait_time} секунд...")
                await asyncio.sleep(wait_time)
            else:
                logging.error(f"Ошибка отправки сообщения: {e}")
                return False
    return False

async def send_results(message: types.Message, results, title, delay=0.7):
    """Отправляет результаты выбранного типа"""
    if not results:
        await message.answer(f"ℹ️ {title}: нет результатов")
        return
    
    total = sum(len(occ) for occ in results.values())
    
    # Отправляем заголовок только один раз
    await safe_send_message(message, f"{title} ({total} нарушений):", delay)
    
    for page_num, occurrences in results.items():
        if not occurrences:
            continue
            
        page_msg = f"📄 Страница {page_num + 1}:\n\n"
        
        for i, occ in enumerate(occurrences, 1):
            gpt_info = f"GPT: {occ.get('gpt_result', {}).get('raw_response', 'нет данных')}"
            
            violation_msg = (f"{i}. ФИО: {occ['full_name']}\n"
                           f"   Найдено: '{occ['matched_word']}'\n"
                           f"   Контекст: {occ['context']}\n"
                           f"   {gpt_info}\n\n")
            
            # Проверяем, не превысит ли добавление лимит
            if len(page_msg) + len(violation_msg) > 4000:
                # Отправляем текущую часть страницы
                await safe_send_message(message, page_msg, delay)
                # Начинаем новое сообщение для продолжения
                page_msg = f"📄 Страница {page_num + 1} (продолжение):\n\n"
                # Добавляем violation_msg к новому сообщению
                page_msg += violation_msg
            else:
                # Просто добавляем к текущему сообщению
                page_msg += violation_msg
        
        # Отправляем оставшуюся часть страницы
        if page_msg.strip() and page_msg != f"📄 Страница {page_num + 1}:\n\n":
            await safe_send_message(message, page_msg, delay)
# Глобальная переменная для хранения результатов

# --- Основная точка входа ---
async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())