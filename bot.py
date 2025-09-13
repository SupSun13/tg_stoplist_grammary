import os
import logging
from aiogram import Bot, Dispatcher, types, F, Router
from aiogram.filters import Command
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from dotenv import load_dotenv
import fitz
from natasha import NamesExtractor, MorphVocab
import pymorphy3
import re
from aiogram.types import CallbackQuery

import openai
import os
from openai import OpenAI 
import os
from dotenv import load_dotenv

load_dotenv()

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY")) 

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


# Получение фамилии
file = open('inoagents_050925.txt', 'r', encoding='utf-8')
stop_list = file.read().split(', ')

file = open('ia_initials_050925.txt', 'r', encoding='utf-8')
initial_slist = file.read().split(', ')

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

# Загружаем переменные окружения из .env файла
load_dotenv()

# Настройка логирования
logging.basicConfig(level=logging.INFO)

router = Router()

# Инициализация бота и диспетчера
bot = Bot(token=os.getenv('BOT_TOKEN'))
dp = Dispatcher()
dp.include_router(router)

# Папка для сохранения PDF (создаем если не существует)
PDF_STORAGE = "received_pdfs"
os.makedirs(PDF_STORAGE, exist_ok=True)

async def check_with_gpt(target_fio, word, context):
    """
    Проверяет через GPT, является ли слово фамилией и тем ли человеком
    """
    try:
        content = f"Целевое ФИО: {target_fio}\nСлово: {word}\nКонтекст: {context}"
        
        completion = client.chat.completions.create(
        model="gpt-5-mini",  # Указываем модель. Также можно использовать "gpt-4"
        messages=[  # messages — это список сообщений, которые составляют историю диалога
            {"role": "system", "content": f"{role}"},
            {"role": "user", "content": f"{content}"}
        ],
        reasoning_effort="low",     # Уровень усилий для reasoning ('low', 'medium', 'high')
        verbosity="low"   
        )
        
        response = completion.choices[0].message.content
        return parse_gpt_response(response)
        
    except Exception as e:
        print(f"Ошибка при запросе к GPT: {e}")
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

@dp.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer("Привет! Отправь мне PDF-файл для обработки.")

async def ask_check_type(message: types.Message, file_path: str):
    """Спрашивает пользователя о типе проверки"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Проверить по стоп-листу", callback_data=f"check_stoplist:{file_path}"),
            InlineKeyboardButton(text="📝 Проверить орфографию и грамматику", callback_data=f"check_spelling:{file_path}")
        ]
    ])
    
    await message.answer("🔍 Выберите тип проверки:", reply_markup=keyboard)

@dp.message(F.document)
async def handle_pdf(message: Message):
    if message.document.mime_type != 'application/pdf':
        await message.answer("Пожалуйста, отправьте PDF файл!")
        return
    
    try:
        # Скачиваем файл
        file = await bot.get_file(message.document.file_id)
        downloaded = await bot.download_file(file.file_path)
        
        filename = message.document.file_name or f"document_{message.document.file_id}.pdf"
        filepath = os.path.join(PDF_STORAGE, filename)
        
        with open(filepath, 'wb') as f:
            f.write(downloaded.getvalue())
        
        await message.answer(f"✅ Файл сохранен!")
        await ask_check_type(message, filepath)  # Спрашиваем тип проверки
        
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")

@router.callback_query(F.data.startswith("check_stoplist:"))
async def handle_check_stoplist(callback: CallbackQuery):
    """Обработчик проверки по стоп-листу"""
    try:
        await callback.answer("⏳ Начинаю проверку по стоп-листу...")
        file_path = callback.data.split(":", 1)[1]
        await process_pdf_pages(file_path, callback.message)  # Ваш существующий код
    except Exception as e:
        logging.error(f"Ошибка в check_stoplist: {e}")
        await callback.message.answer("❌ Ошибка при проверке по стоп-листу")

@router.callback_query(F.data.startswith("check_spelling:"))
async def handle_check_spelling(callback: CallbackQuery):
    """Обработчик проверки орфографии и грамматики"""
    try:
        await callback.answer("⏳ Начинаю проверку орфографии...")
        file_path = callback.data.split(":", 1)[1]
        await check_spelling_grammar(file_path, callback.message)
    except Exception as e:
        logging.error(f"Ошибка в check_spelling: {e}")
        await callback.message.answer("❌ Ошибка при проверке орфографии")

async def check_spelling_grammar(file_path: str, message: types.Message):
    """Проверяет орфографию и грамматику в PDF"""
    try:
        doc = fitz.open(file_path)
        total_pages = doc.page_count
        
        await message.answer(f"📄 Обнаружено страниц: {total_pages}")
        await message.answer("🔍 Начинаю проверку орфографии и грамматики...")
        
        all_errors = []
        
        for page_num in range(15):
            page = doc.load_page(page_num)
            text = page.get_text()
            
            if text.strip():
                page_errors = await check_page_spelling(text, page_num + 1)
                all_errors.extend(page_errors)
                
                # Отправляем прогресс каждые 5 страниц
                if (page_num + 1) % 5 == 0:
                    await message.answer(f"⏳ Обработано {page_num + 1}/{total_pages} страниц")
        
        doc.close()
        
        # Отправляем результаты
        await send_spelling_results(message, all_errors)
        
    except Exception as e:
        logging.error(f"Ошибка проверки орфографии: {e}")
        await message.answer("❌ Ошибка при проверке орфографии")

async def check_page_spelling(text: str, page_num: int) -> list:
    """Проверяет орфографию на одной странице через GPT"""
    spelling_role = '''
Ты — лингвистический эксперт по русскому языку. Проанализируй текст и найди все орфографические и грамматические ошибки.

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

    try:
        completion = client.chat.completions.create(
            model="gpt-5-nano",
            messages=[
                {"role": "system", "content": spelling_role},
                {"role": "user", "content": f"Проверь текст:\n{text}"}
            ],
            reasoning_effort="low",     # Уровень усилий для reasoning ('low', 'medium', 'high')
            verbosity="low"
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
                        'page': page_num,
                        'type': error_type,
                        'left_context': left_context.strip(),
                        'wrong_word': wrong_word.strip(),
                        'right_context': right_context.strip(),
                        'correction': correction.strip(),
                        'full_context': f"{left_context} {wrong_word} {right_context}"
                    })
        
        return errors
        
    except Exception as e:
        logging.error(f"Ошибка GPT при проверке страницы {page_num}: {e}")
        return []

async def send_spelling_results(message: types.Message, errors: list):
    """Отправляет результаты проверки орфографии"""
    if not errors:
        await message.answer("✅ Орфографических и грамматических ошибок не найдено!")
        return
    
    # Группируем ошибки по страницам
    errors_by_page = {}
    for error in errors:
        page = error['page']
        if page not in errors_by_page:
            errors_by_page[page] = []
        errors_by_page[page].append(error)
    
    # Отправляем сводку
    await message.answer(f"📊 Найдено ошибок: {len(errors)}")
    
    # Отправляем ошибки по страницам
    for page_num, page_errors in errors_by_page.items():
        page_msg = f"📄 Страница {page_num}:\n\n"
        
        for i, error in enumerate(page_errors, 1):
            error_msg = (f"{i}. {error['type'].upper()}: {error['full_context']}\n"
                       f"   ❌ Неправильно: {error['wrong_word']}\n"
                       f"   ✅ Правильно: {error['correction']}\n\n")
            
            if len(page_msg) + len(error_msg) > 4000:
                await message.answer(page_msg)
                page_msg = f"📄 Страница {page_num} (продолжение):\n\n"
            
            page_msg += error_msg
        
        if page_msg.strip():
            await message.answer(page_msg)
            await asyncio.sleep(0.7)


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

analysis_results = {}

@router.callback_query(F.data == "show_all")
async def handle_show_all(callback: CallbackQuery):
    """Обработчик для кнопки 'show_all'"""
    try:
        await callback.answer("⏳ Подготавливаю результаты...")
        results = analysis_results.get('all', {})
        await send_results(callback.message, results, "📊 Все результаты", delay=0.7)
    except Exception as e:
        logging.error(f"Ошибка в show_all: {e}")
        await callback.answer("❌ Ошибка при обработке")

@router.callback_query(F.data == "show_verified")
async def handle_show_verified(callback: CallbackQuery):
    try:
        await callback.answer("⏳ Подготавливаю подтвержденные...")
        results = analysis_results.get('verified', {})
        await send_results(callback.message, results, "✅ Подтвержденные", delay=0.7)
    except Exception as e:
        logging.error(f"Ошибка в show_verified: {e}")
        await callback.answer("❌ Ошибка")

@router.callback_query(F.data == "show_unknown")
async def handle_show_unknown(callback: CallbackQuery):
    try:
        await callback.answer("⏳ Подготавливаю неизвестные...")
        results = analysis_results.get('unknown', {})
        await send_results(callback.message, results, "❓ Неизвестные", delay=0.7)
    except Exception as e:
        logging.error(f"Ошибка в show_unknown: {e}")
        await callback.answer("❌ Ошибка")

@router.callback_query(F.data == "show_namesake")
async def handle_show_namesake(callback: CallbackQuery):
    try:
        await callback.answer("⏳ Подготавливаю однофамильцев...")
        results = analysis_results.get('namesake', {})
        await send_results(callback.message, results, "👥 Однофамильцы", delay=0.7)
    except Exception as e:
        logging.error(f"Ошибка в show_namesake: {e}")
        await callback.answer("❌ Ошибка")
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


async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())