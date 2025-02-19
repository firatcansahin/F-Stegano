from flask import Flask, render_template, request, jsonify
from werkzeug.utils import secure_filename
import os
import re
import docx  # Word dosyaları için
from PyPDF2 import PdfReader  # PDF dosyaları için
import unicodedata
import pefile
import binascii

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = 1024 * 1024 * 1024  # 1GB limit

# Desteklenen dosya türleri
ALLOWED_MEDIA = {'png', 'jpg', 'jpeg', 'gif', 'bmp', 'mp4', 'avi', 'mov'}
ALLOWED_DOCUMENTS = {'doc', 'docx', 'pdf', 'txt'}

if not os.path.exists(app.config['UPLOAD_FOLDER']):
    os.makedirs(app.config['UPLOAD_FOLDER'])

def allowed_file(filename, file_type='media'):
    extension = filename.rsplit('.', 1)[1].lower() if '.' in filename else ''
    if file_type == 'media':
        return extension in ALLOWED_MEDIA
    return extension in ALLOWED_DOCUMENTS

def normalize_filename(filename):
    """Dosya adındaki özel karakterleri koruyarak normalize eder"""
    try:
        # Unicode normalizasyonu yap ama karakterleri değiştirme
        normalized = unicodedata.normalize('NFKC', filename)
        # Sadece tehlikeli karakterleri temizle
        safe_chars = re.sub(r'[<>:"/\\|?*]', '', normalized)
        return safe_chars
    except:
        return filename

def read_document_content(file_path):
    extension = file_path.split('.')[-1].lower()
    try:
        if extension == 'pdf':
            reader = PdfReader(file_path)
            text = ''
            for page in reader.pages:
                text += page.extract_text() + '\n'
            return text
        elif extension in ['doc', 'docx']:
            doc = docx.Document(file_path)
            return '\n'.join([paragraph.text for paragraph in doc.paragraphs])
        elif extension == 'txt':
            # UTF-8 ve diğer kodlamaları dene
            encodings = ['utf-8', 'cp1251', 'windows-1251', 'koi8-r', 'iso-8859-5']
            for encoding in encodings:
                try:
                    with open(file_path, 'r', encoding=encoding) as f:
                        return f.read()
                except UnicodeDecodeError:
                    continue
            # Son çare olarak binary oku ve decode et
            with open(file_path, 'rb') as f:
                return f.read().decode('utf-8', errors='ignore')
    except Exception as e:
        print(f"Hata: {str(e)}")
        return ""

def find_common_text_in_documents(file_contents):
    if len(file_contents) < 2:
        return "En az 2 dosya yüklemelisiniz."
    
    all_matches = []
    patterns = [
        r'[A-Za-z0-9+/]{4,}={0,2}',  # Base64
        r'[A-Fa-f0-9]{8,}',          # Hex
        r'[A-Za-z0-9!@#$%^&*()_+\-=\[\]{};:,.<>?]{4,}',  # Özel karakterler
        r'(?:[0-9]+[A-Za-z]+|[A-Za-z]+[0-9]+)[A-Za-z0-9]*',  # Alfanumerik karışım
        r'[!@#$%^&*()_+\-=\[\]{};:,.<>?]{2,}[A-Za-z0-9]+',  # Özel karakter + Alfanumerik
        r'\\x[0-9a-fA-F]{2}',        # Escaped hex
        r'%[0-9A-Fa-f]{2}'           # URL encoded
    ]
    
    for content in file_contents:
        if isinstance(content, str):
            matches = set()
            for pattern in patterns:
                matches.update(re.findall(pattern, content, re.UNICODE))
            if matches:
                all_matches.append(matches)
    
    if not all_matches:
        return "Ortak metin bulunamadı."
    
    common_texts = all_matches[0]
    for matches in all_matches[1:]:
        common_texts = common_texts.intersection(matches)
    
    if common_texts:
        sorted_texts = sorted(common_texts, key=lambda x: (-len(x), x))
        filtered_texts = [text for text in sorted_texts if len(text) >= 4]
        return "\n".join(filtered_texts)
    else:
        return "Ortak metin bulunamadı."

def find_common_text_in_media(file_contents, filenames):
    if len(file_contents) < 1:
        return "En az 1 dosya yüklemelisiniz."
    
    results = []
    
    # Her dosya için alfanumerik metinleri bul
    for content, filename in zip(file_contents, filenames):
        try:
            # Binary içeriği text'e çevir
            try:
                text = content.decode('utf-8', errors='ignore')
            except:
                text = content.hex()
            
            # Sadece alfanumerik karakterlerden oluşan ve en az 5 karakter uzunluğunda olan metinleri bul
            pattern = r'[a-zA-Z0-9]{10,}'
            matches = re.findall(pattern, text)
            
            # Bulunan her eşleşme için dosya adıyla birlikte kaydet
            for match in matches:
                results.append(f"{match} (Dosya: {filename})")
        
        except Exception as e:
            print(f"Dosya işleme hatası: {str(e)}")
            continue

    if results:
        # Tekrarlanan sonuçları kaldır ve alfabetik sırala
        unique_results = sorted(set(results))
        return "\n".join(unique_results)
    else:
        return "Alfanumerik metin bulunamadı."

def find_common_text_in_source(file_contents):
    if len(file_contents) < 2:
        return "En az 2 dosya yüklemelisiniz."
    
    potential_texts = set()
    patterns = [
        r'[A-Za-z0-9+/]{4,}={0,2}',  # Base64
        r'[A-Fa-f0-9]{8,}',          # Hex
        r'[A-Za-z0-9!@#$%^&*()_+\-=\[\]{};:,.<>?]{4,}',  # Özel karakterler
        r'(?:[0-9]+[A-Za-z]+|[A-Za-z]+[0-9]+)[A-Za-z0-9]*',  # Alfanumerik karışım
        r'[!@#$%^&*()_+\-=\[\]{};:,.<>?]{2,}[A-Za-z0-9]+',  # Özel karakter + Alfanumerik
        r'\\x[0-9a-fA-F]{2}',        # Escaped hex
        r'%[0-9A-Fa-f]{2}'           # URL encoded
    ]
    
    for content in file_contents:
        try:
            chunk_size = 200
            for i in range(0, len(content) - chunk_size, 50):
                chunk = content[i:i + chunk_size]
                for encoding in ['utf-8', 'cp1251', 'ascii', 'iso-8859-1']:
                    try:
                        text = chunk.decode(encoding, errors='ignore')
                        for pattern in patterns:
                            matches = re.findall(pattern, text)
                            potential_texts.update(matches)
                        break
                    except:
                        continue
        except Exception as e:
            print(f"Hata: {str(e)}")
            continue

    if not potential_texts:
        return "Ortak metin bulunamadı."

    common_texts = set(potential_texts)
    for content in file_contents:
        try:
            found = False
            for encoding in ['utf-8', 'cp1251', 'ascii', 'iso-8859-1']:
                try:
                    content_str = content.decode(encoding, errors='ignore')
                    common_texts = {text for text in common_texts if text in content_str}
                    found = True
                    break
                except:
                    continue
            if not found:
                content_hex = content.hex()
                common_texts = {text for text in common_texts if text in content_hex}
        except:
            continue

    if common_texts:
        sorted_texts = sorted(common_texts, key=lambda x: (-len(x), x))
        filtered_texts = [text for text in sorted_texts if len(text) >= 4]
        return "\n".join(filtered_texts)
    else:
        return "Ortak metin bulunamadı."

def find_hash_patterns(file_contents, filenames):
    """Hash benzeri metinleri bulur (MD5, SHA vb.)"""
    if not file_contents:
        return "Dosya içeriği boş"
    
    results = []
    
    # Her dosya için alfanumerik metinleri bul
    for content, filename in zip(file_contents, filenames):
        try:
            # Binary içeriği farklı kodlamalarla text'e çevirmeyi dene
            text_versions = []
            
            # Hex versiyonunu ekle
            text_versions.append(content.hex())
            
            # Farklı kodlamalarla deneme yap
            for encoding in ['utf-8', 'cp1251', 'windows-1251', 'koi8-r', 'iso-8859-5', 'ascii']:
                try:
                    decoded = content.decode(encoding, errors='ignore')
                    if decoded:
                        text_versions.append(decoded)
                except:
                    continue
            
            # Her text versiyonunda ara
            for text in text_versions:
                # Sadece alfanumerik karakterlerden oluşan ve en az 10 karakter uzunluğunda olan metinleri bul
                pattern = r'[a-zA-Z0-9]{10,}'
                matches = re.findall(pattern, text)
                
                # Bulunan her eşleşme için dosya adıyla birlikte kaydet
                for match in matches:
                    # Dosya adını orijinal haliyle kullan
                    result = f"{match} (Dosya: {filename})"
                    if result not in results:  # Tekrarları önle
                        results.append(result)
        
        except Exception as e:
            print(f"Hash arama hatası ({filename}): {str(e)}")
            continue

    if results:
        # Sonuçları alfabetik sırala
        return "\n".join(sorted(set(results)))
    else:
        return "Alfanumerik metin bulunamadı."

def analyze_exe(file_contents, filenames):
    """EXE dosyalarını analiz eder ve içindeki stringleri bulur"""
    if not file_contents:
        return "Dosya içeriği boş"
    
    results = []
    
    for content, filename in zip(file_contents, filenames):
        try:
            # Geçici dosya oluştur
            temp_path = os.path.join(app.config['UPLOAD_FOLDER'], 'temp.exe')
            with open(temp_path, 'wb') as f:
                f.write(content)
            
            # PE dosyasını analiz et
            pe = pefile.PE(temp_path)
            
            # Sections bilgilerini al
            sections_info = []
            for section in pe.sections:
                section_name = section.Name.decode('utf-8', 'ignore').strip('\x00')
                section_data = binascii.hexlify(section.get_data()[:50]).decode('utf-8')
                sections_info.append(f"Bölüm: {section_name}")
                
                # Bölüm içindeki alfanumerik stringleri ara
                data = section.get_data()
                try:
                    text = data.decode('utf-8', 'ignore')
                    pattern = r'[a-zA-Z0-9]{10,}'
                    matches = re.findall(pattern, text)
                    for match in matches:
                        results.append(f"{match} (Dosya: {filename}, Bölüm: {section_name})")
                except:
                    continue
            
            # Import bilgilerini al
            if hasattr(pe, 'DIRECTORY_ENTRY_IMPORT'):
                for entry in pe.DIRECTORY_ENTRY_IMPORT:
                    dll_name = entry.dll.decode('utf-8', 'ignore')
                    sections_info.append(f"Import DLL: {dll_name}")
            
            # Genel dosya bilgilerini ekle
            results.append(f"\nDosya Bilgileri ({filename}):")
            results.append("------------------------")
            results.extend(sections_info)
            results.append("")  # Boş satır ekle
            
        except Exception as e:
            results.append(f"Hata ({filename}): {str(e)}")
        finally:
            # Geçici dosyayı temizle
            if os.path.exists(temp_path):
                os.remove(temp_path)
    
    if results:
        return "\n".join(results)
    else:
        return "Analiz edilebilir içerik bulunamadı."

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/upload', methods=['POST'])
def upload_file():
    if 'files[]' not in request.files:
        return jsonify({'error': 'Dosya seçilmedi'}), 400
    
    files = request.files.getlist('files[]')
    if not files or all(file.filename == '' for file in files):
        return jsonify({'error': 'Dosya seçilmedi'}), 400

    file_type = request.form.get('type', 'media')
    file_contents = []
    filenames = []
    
    for file in files:
        try:
            # Orijinal dosya adını koru
            filename = file.filename
            safe_filename = normalize_filename(filename)
            file_path = os.path.join(app.config['UPLOAD_FOLDER'], safe_filename)
            
            file.save(file_path)
            
            with open(file_path, 'rb') as f:
                content = f.read()
                if content:
                    file_contents.append(content)
                    filenames.append(filename)  # Orijinal dosya adını sakla
        except Exception as e:
            print(f"Dosya işleme hatası: {str(e)}")
        finally:
            if os.path.exists(file_path):
                os.remove(file_path)
    
    if not file_contents:
        return jsonify({'error': 'Geçerli dosya bulunamadı'}), 400
    
    # İşlem tipine göre analiz yap
    if file_type == 'media':
        result = find_common_text_in_media(file_contents, filenames)
    elif file_type == 'document':
        if len(file_contents) < 2:
            return jsonify({'error': 'En az 2 dosya gerekli'}), 400
        result = find_common_text_in_documents(file_contents)
    elif file_type == 'source':
        if len(file_contents) < 2:
            return jsonify({'error': 'En az 2 dosya gerekli'}), 400
        result = find_common_text_in_source(file_contents)
    elif file_type == 'hash':
        result = find_hash_patterns(file_contents, filenames)
    elif file_type == 'exe':
        result = analyze_exe(file_contents, filenames)
    
    return jsonify({'message': result})

if __name__ == '__main__':
    app.run(debug=True) 