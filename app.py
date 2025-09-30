# app.py
from flask import Flask, request, render_template, redirect, url_for, flash, session, jsonify
import os
import logging
import tempfile
import uuid
from werkzeug.utils import secure_filename
from dotenv import load_dotenv
import fitz # PyMuPDF для PDF
from openai import OpenAI # Обновлённый клиент OpenAI
import pymorphy3
import re
import asyncio as sync_asyncio # Переименуем, чтобы не путать с asyncio.run
import json # Добавляем json для сохранения результатов

# Загружаем переменные окружения
load_dotenv()

# --- Копирование и адаптация функций из bot2qwen.txt ---
# (Вставьте сюда функции: create_last_name_variations, universal_extract_last_name,
# create_full_names_map, find_full_name_in_text, variations_by_name,
# parse_gpt_response)

# --- ВАЖНО: Убедитесь, что у вас есть доступ к OpenAI API ---
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise ValueError("OPENAI_API_KEY не найден в .env файле")
client = OpenAI(api_key=OPENAI_API_KEY)

# --- Копируем роли GPT ---
ROLE_GPT = '''
Ты — лингвистический ассистент, специализирующийся на анализе имен собственных. Твоя задача — анализировать заданный текст (контекст) и определять, является ли указанное в вопросе слово в этом контексте фамилией (именем собственным), а не прилагательным или другой частью речи.
# Инструкции
1.  **Входные данные:** Тебе будут предоставлены:
    *   `[Целевое ФИО]`: Полное ФИО человека для сравнения (например, "Иванов Иван Иванович").
    *   `[Слово]`: Слово, которое нужно проанализировать.
    *   `[Контекст]`: Предложение или текст для анализа.
2.  **Шаг 1: Определение фамилии**
    *   Внимательно изучи `[Контекст]`. Определи, используется ли `[Слово]` в данном контексте как фамилия (имя собственное) или как прилагательное/нарицательное существительное (имя нарицательное).
    *   *Пример: В контексте "Завод производит стальные трубы" слово "стальные" — прилагательное. В контеке "Инженер Стальной получил премию" слово "Стальной" — фамилия.*
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

# --- Новый промт для проверки орфографии/грамматики (обновлённый) ---
SPELLING_ROLE_TEX = '''
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

# --- Загрузка списков ---
try:
    with open('inoagents_050925.txt', 'r', encoding='utf-8') as f:
        stop_list = [name.strip() for name in f.read().split(', ')]
    with open('ia_initials_050925.txt', 'r', encoding='utf-8') as f:
        initial_slist = [name.strip() for name in f.read().split(', ')]
except FileNotFoundError as e:
    print(f"Ошибка: файл не найден - {e}")
    stop_list = []
    initial_slist = []

# --- Функции из бота (адаптированы) ---

def create_last_name_variations(last_name):
    morph = pymorphy3.MorphAnalyzer()
    variations = set()
    parsed = morph.parse(last_name)[0]
    variations.add(parsed.normal_form.lower())
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
    if not full_name or not isinstance(full_name, str):
        return None
    cleaned_name = re.sub(r'"[^"]*"', '', full_name)
    cleaned_name = re.sub(r'\s+', ' ', cleaned_name).strip()
    if not cleaned_name:
        return None
    words = cleaned_name.split()
    if not words:
        return None
    return words[0]

def create_full_names_map(initial_slist):
    full_names_map = {}
    for full_name in initial_slist:
        surname = universal_extract_last_name(full_name)
        if surname:
            full_names_map[surname] = full_name
    return full_names_map

def find_full_name_in_text(text, target_variations_dict, full_names_map):
    """
    Ищет фамилию в тексте и возвращает список найденных вхождений с контекстом
    target_variations_dict: результат функции variations_by_name, т.е. {variation: original_surname}
    """
    if not text or not target_variations_dict or not full_names_map:
        return []
    words = re.findall(r'\b[а-яёА-ЯЁ]+\b', text)
    lower_words = [word.lower() for word in words]
    results = []
    for i, word in enumerate(lower_words):
        if word in target_variations_dict: # Теперь ищем в словаре var -> orig
            start_idx = max(0, i - 5)
            end_idx = min(len(words), i + 6)
            context = ' '.join(words[start_idx:end_idx])
            original_surname = target_variations_dict[word] # Получаем оригинальную фамилию
            full_name = full_names_map.get(original_surname, original_surname) # Получаем полное ФИО
            results.append({
                'full_name': full_name,
                'surname': original_surname,
                'matched_word': words[i],
                'context': context,
                'position': i,
                'original': full_name
            })
    return results

def variations_by_name(target_last_names):
    all_variations = {}
    for last_name in target_last_names:
        variations = create_last_name_variations(last_name)
        for variation in variations:
            all_variations[variation] = last_name  # Сохраняем оригинальную фамилию
    return all_variations

# --- Обновлённые асинхронные функции GPT (принимают модель) ---

async def _check_with_gpt_async(target_fio, word, context, model="gpt-5-mini"):
    try:
        content = f"Целевое ФИО: {target_fio}\nСлово: {word}\nКонтекст: {context}"
        completion = client.chat.completions.create(
            model=model, # Используем переданную модель
            messages=[
                {"role": "system", "content": ROLE_GPT},
                {"role": "user", "content": content}
            ],
            # reasoning_effort и verbosity могут быть не поддержаны всеми моделями
            # Убираем их или используем только если точно знаем, что модель поддерживает
        )
        response = completion.choices[0].message.content
        return parse_gpt_response(response)
    except Exception as e:
        print(f"Ошибка при запросе к GPT (стоп-лист, модель {model}): {e}")
        return None

def check_with_gpt_sync(target_fio, word, context, model="gpt-5-mini"):
    """Синхронная версия для Flask (использует asyncio.run)"""
    try:
        loop = sync_asyncio.new_event_loop()
        sync_asyncio.set_event_loop(loop)
        result = loop.run_until_complete(_check_with_gpt_async(target_fio, word, context, model))
        loop.close()
        return result
    except Exception as e:
        print(f"Ошибка при синхронном вызове GPT (стоп-лист, модель {model}): {e}")
        return None

def parse_gpt_response(response):
    result = {
        'is_surname': False,
        'match_type': '-',
        'raw_response': response
    }
    if not response:
        return result
    lines = response.strip().split('\n')
    for line in lines:
        if line.startswith('фамилия:'):
            result['is_surname'] = 'да' in line.lower()
        elif line.startswith('совпадение:'):
            match_part = line.split(':', 1)[1].strip()
            result['match_type'] = match_part
    return result

# --- Новые функции для обработки .tex файлов (принимают модель и размер батча) ---

def create_tex_batches(file_path: str, max_size: int = 5000) -> list: # max_size теперь параметр
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

async def _check_spelling_grammar_text_async(text: str, batch_num: int = None, model="gpt-5-nano") -> list: # model теперь параметр
    """Проверяет орфографию и грамматику в тексте (батче) через GPT"""
    batch_info = f" (Батч {batch_num})" if batch_num is not None else ""
    try:
        completion = client.chat.completions.create(
            model=model, # Используем переданную модель
            messages=[
                {"role": "system", "content": SPELLING_ROLE_TEX},
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
                        'batch': batch_num, # Используем batch_num вместо page_num для .tex
                        'type': error_type,
                        'left_context': left_context.strip(),
                        'wrong_word': wrong_word.strip(),
                        'right_context': right_context.strip(),
                        'correction': correction.strip(),
                        'full_context': f"{left_context} {wrong_word} {right_context}"
                    })
        return errors
    except Exception as e:
        logging.error(f"Ошибка GPT при проверке орфографии{batch_info} (модель {model}): {e}")
        return []

def check_spelling_grammar_text_sync(text: str, batch_num: int = None, model="gpt-5-nano") -> list: # model теперь параметр
    """Синхронная версия проверки орфографии/грамматики (использует asyncio.run)"""
    try:
        loop = sync_asyncio.new_event_loop()
        sync_asyncio.set_event_loop(loop)
        result = loop.run_until_complete(_check_spelling_grammar_text_async(text, batch_num, model))
        loop.close()
        return result
    except Exception as e:
        logging.error(f"Ошибка при синхронном вызове GPT (орфография, модель {model}): {e}")
        return []


# --- Настройка Flask ---
app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'your_default_secret_key_which_should_be_long_and_random_and_kept_secret_in_prod') # Установите надежный ключ в .env!
UPLOAD_FOLDER = 'uploads'
RESULTS_FOLDER = 'results' # Новая папка для результатов
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['RESULTS_FOLDER'] = RESULTS_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024 # Максимальный размер файла 16MB

# Убедитесь, что папки существуют
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(RESULTS_FOLDER, exist_ok=True) # Создаём папку для результатов

# --- Маршруты Flask ---

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/upload', methods=['POST'])
def upload_file():
    if 'document_file' not in request.files: # Изменили имя поля в форме
        flash('Файл не выбран')
        return redirect(request.url)
    file = request.files['document_file']
    if file.filename == '':
        flash('Файл не выбран')
        return redirect(request.url)

    # Проверяем расширение файла
    if file and (file.filename.lower().endswith('.pdf') or file.filename.lower().endswith('.tex')):
        filename = secure_filename(file.filename)
        unique_filename = f"{uuid.uuid4()}_{filename}"
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
        file.save(filepath)

        session['doc_path'] = filepath
        session['doc_type'] = 'pdf' if file.filename.lower().endswith('.pdf') else 'tex'

        check_type = request.form.get('check_type')
        session['check_type'] = check_type

        # --- Сохраняем параметры из формы ---
        gpt_model = request.form.get('gpt_model', 'gpt-5-mini') # По умолчанию gpt-5-mini
        try:
            batch_size = int(request.form.get('batch_size', 5000)) # По умолчанию 5000
            if batch_size < 100 or batch_size > 50000:
                flash('❌ Размер батча должен быть от 100 до 50000.')
                return redirect(request.url)
        except ValueError:
            flash('❌ Неверный формат размера батча.')
            return redirect(request.url)

        session['gpt_model'] = gpt_model
        session['batch_size'] = batch_size

        return redirect(url_for('process_file'))
    else:
        flash('Неверный формат файла. Пожалуйста, загрузите PDF или TEX.')
        return redirect(request.url)

@app.route('/process')
def process_file():
    doc_path = session.get('doc_path')
    doc_type = session.get('doc_type')
    check_type = session.get('check_type')
    # --- Получаем параметры из сессии ---
    gpt_model = session.get('gpt_model', 'gpt-5-mini')
    batch_size = session.get('batch_size', 5000)

    if not doc_path or not os.path.exists(doc_path) or not doc_type:
        flash('Файл не найден или тип файла не определен. Пожалуйста, загрузите снова.')
        return redirect(url_for('index'))

    if check_type == 'check_stoplist':
        # --- Логика проверки по стоп-листу ---
        try:
            all_results = {}
            verified_results = {}
            unknown_results = {}
            namesake_results = {}

            full_names_map = create_full_names_map(initial_slist)
            variations = variations_by_name(stop_list) # {variation: original_surname}

            if doc_type == 'pdf':
                doc = fitz.open(doc_path)
                total_items = doc.page_count
                print(f"Обнаружено страниц PDF: {total_items}")

                for item_num in range(total_items):
                    page = doc.load_page(item_num)
                    text = page.get_text()
                    occurrences = find_full_name_in_text(text, variations, full_names_map)
                    if occurrences:
                        all_results[item_num] = occurrences
                        verified_occurrences = []
                        unknown_occurrences = []
                        namesake_occurrences = []

                        for occ in occurrences:
                            # --- Передаём модель ---
                            gpt_result = check_with_gpt_sync(occ['full_name'], occ['matched_word'], occ['context'], model=gpt_model)
                            if gpt_result:
                                occ['gpt_result'] = gpt_result
                                if gpt_result['match_type'] == 'target_person':
                                    verified_occurrences.append(occ)
                                elif gpt_result['match_type'] == 'неизвестно':
                                    unknown_occurrences.append(occ)
                                elif gpt_result['match_type'] == 'однофамилец':
                                    namesake_occurrences.append(occ)

                        if verified_occurrences:
                            verified_results[item_num] = verified_occurrences
                        if unknown_occurrences:
                            unknown_results[item_num] = unknown_occurrences
                        if namesake_occurrences:
                            namesake_results[item_num] = namesake_occurrences

                doc.close()

            elif doc_type == 'tex':
                # Для .tex при проверке стоп-листа обработка не предусмотрена в bot2qwen.txt
                # Возвращаем ошибку или сообщение
                flash('⚠️ Проверка по стоп-листу для .tex файлов пока не реализована.')
                return redirect(url_for('index'))

            # --- СОХРАНЕНИЕ РЕЗУЛЬТАТОВ СТОП-ЛИСТА ---
            # Генерируем уникальный ID для результатов
            results_id = str(uuid.uuid4())
            results_file_path = os.path.join(app.config['RESULTS_FOLDER'], f"analysis_{results_id}.json")
            results_data = {
                'verified': verified_results,
                'unknown': unknown_results,
                'namesake': namesake_results,
                'all': all_results
            }
            with open(results_file_path, 'w', encoding='utf-8') as f:
                json.dump(results_data, f, ensure_ascii=False, indent=4)

            # Сохраняем ID и summary в сессии
            session['analysis_results_id'] = results_id
            session['analysis_summary'] = {
                'verified': sum(len(v) for v in verified_results.values()),
                'unknown': sum(len(u) for u in unknown_results.values()),
                'namesake': sum(len(n) for n in namesake_results.values()),
                'total_found': sum(len(a) for a in all_results.values()),
                'total_items': total_items, # Количество страниц или 1 для .tex (но .tex не обрабатывается здесь)
                'doc_type': doc_type,
                'gpt_model': gpt_model # Сохраняем модель в summary
            }

        except Exception as e:
            logging.error(f"Ошибка при проверке стоп-листа: {e}")
            flash(f'Ошибка при проверке стоп-листа: {e}')
            return redirect(url_for('index'))

    elif check_type == 'check_spelling':
        # --- Логика проверки орфографии ---
        try:
            all_errors = []

            if doc_type == 'pdf':
                doc = fitz.open(doc_path)
                total_items = doc.page_count
                print(f"Обнаружено страниц PDF: {total_items}")

                for item_num in range(total_items):
                    page = doc.load_page(item_num)
                    text = page.get_text()
                    if text.strip():
                        # --- Передаём модель ---
                        page_errors = check_spelling_grammar_text_sync(text, item_num + 1, model=gpt_model)
                        all_errors.extend(page_errors)

                doc.close()

            elif doc_type == 'tex':
                 print(f"Обнаружен TEX файл, разбивается на батчи (размер {batch_size}) для проверки орфографии.")
                 # --- НОВАЯ ЛОГИКА ДЛЯ .TEX (орфография) ---
                 # --- Передаём размер батча ---
                 batches = create_tex_batches(doc_path, max_size=batch_size) # Передаём batch_size
                 total_items = len(batches)
                 print(f"Разбито на {total_items} батчей для проверки орфографии.")

                 if not batches:
                     flash('❌ Не удалось создать батчи из .tex файла.')
                     return redirect(url_for('index'))

                 for batch_num, batch_text in enumerate(batches):
                     if batch_text.strip(): # Проверяем, что батч не пустой
                         # Используем номер батча (batch_num + 1) для отчета
                         # --- Передаём модель ---
                         batch_errors = check_spelling_grammar_text_sync(batch_text, batch_num + 1, model=gpt_model) # batch_num начинается с 0
                         all_errors.extend(batch_errors)

            # --- СОХРАНЕНИЕ РЕЗУЛЬТАТОВ ОРФОГРАФИИ ---
            # Генерируем уникальный ID для результатов
            results_id = str(uuid.uuid4())
            results_file_path = os.path.join(app.config['RESULTS_FOLDER'], f"spelling_{results_id}.json")
            # Сохраняем список ошибок в JSON файл
            with open(results_file_path, 'w', encoding='utf-8') as f:
                json.dump(all_errors, f, ensure_ascii=False, indent=4)

            # Сохраняем ID и summary в сессии
            session['spelling_results_id'] = results_id
            session['spelling_summary'] = {
                'total_errors': len(all_errors),
                'total_items': total_items, # Количество страниц или батчей .tex
                'doc_type': doc_type,
                'gpt_model': gpt_model, # Сохраняем модель в summary
                'batch_size': batch_size # Сохраняем размер батча в summary
            }

        except Exception as e:
            logging.error(f"Ошибка при проверке орфографии: {e}")
            flash(f'Ошибка при проверке орфографии: {e}')
            return redirect(url_for('index'))

    else:
        flash('Неизвестный тип проверки.')
        return redirect(url_for('index'))

    return redirect(url_for('results'))

@app.route('/results')
def results():
    check_type = session.get('check_type')

    if check_type == 'check_stoplist':
        # Получаем ID результатов из сессии
        results_id = session.get('analysis_results_id')
        if not results_id:
            flash('Результаты не найдены.')
            return redirect(url_for('index'))

        # Формируем путь к файлу
        results_file_path = os.path.join(app.config['RESULTS_FOLDER'], f"analysis_{results_id}.json")
        if not os.path.exists(results_file_path):
            flash('Файл с результатами не найден.')
            return redirect(url_for('index'))

        # Загружаем результаты из файла
        with open(results_file_path, 'r', encoding='utf-8') as f:
            results = json.load(f)

        summary = session.get('analysis_summary', {})
        return render_template('results.html', results=results, summary=summary, check_type=check_type)

    elif check_type == 'check_spelling':
        # Получаем ID результатов из сессии
        results_id = session.get('spelling_results_id')
        if not results_id:
            flash('Результаты не найдены.')
            return redirect(url_for('index'))

        # Формируем путь к файлу
        results_file_path = os.path.join(app.config['RESULTS_FOLDER'], f"spelling_{results_id}.json")
        if not os.path.exists(results_file_path):
            flash('Файл с результатами не найден.')
            return redirect(url_for('index'))

        # Загружаем результаты из файла
        with open(results_file_path, 'r', encoding='utf-8') as f:
            errors = json.load(f)

        summary = session.get('spelling_summary', {})
        return render_template('results.html', spelling_errors=errors, spelling_summary=summary, check_type=check_type)

    else:
        flash('Результаты недоступны.')
        return redirect(url_for('index'))

# --- Запуск приложения ---
if __name__ == '__main__':
    app.run(debug=True) # Не используйте debug=True в продакшене!
