from operator import itemgetter
import os
import pandas as pd
import numpy as np
import tiktoken
from ast import literal_eval
from bs4 import BeautifulSoup
import anthropic  # Changed from openai
from docx import Document
from pypdf import PdfReader
import requests
import json
import re

# Import local embeddings instead of OpenAI
from local_embeddings import get_embedding, calculate_similarity, search_docs
import claude_client  # Replace chatgpt import

with open('/home/drkeithcox/canvas-secrets.key', 'r') as file:
    my_list = [line.strip() for line in file]

API_URL = my_list[0]
API_TOKEN = my_list[1]
ANTHROPIC_API_KEY = my_list[2] if len(my_list) >= 3 else None

# Headers for Canvas API
headers = {
    'Authorization': f'Bearer {API_TOKEN}'
}

# Initialize Anthropic client
client = claude_client.get_client()
api_key = claude_client.get_key()

# Global variables (same as before)
domain = "../db/Transcripts/"
subdomain109 = "../db/Transcripts/109/"
subdomain110 = "../db/Transcripts/110/"
full_url = "../db/Transcripts/"
suburl109 = "../db/Transcripts/109"
suburl110 = "../db/Transcripts/110"
max_tokens = 500
lecStrList = []
dscStrList = []
shortened = []
df = []
df_embeddings = []
df_similarities = []
prmtTitleList = []
qFileList109 = []
qFileList110 = []
aFileList109 = []
aFileList110 = []
US1List = []
US2List = []
US1DList = []
US2DList = []
gradeMode = True
examMode = False
directory = './submissions/'

def extract_content(html_content):
    soup = BeautifulSoup(html_content, 'html.parser')
    name = soup.find('h1').get_text(strip=True)
    extracted_text = ''
    
    for sibling in soup.find('h1').next_siblings:
        if sibling.name in ['p', 'ol', 'div']:
            extracted_text += sibling.get_text(" ", strip=True) + ' '
    
    extracted_text = extracted_text.strip()
    return name, extracted_text

def read_docx(file_path):
    doc = Document(file_path)
    full_text = []
    for para in doc.paragraphs:
        full_text.append(para.text)
    newText = '\n'.join(full_text)
    text = newText.replace('\n', ' ')   
    return text

def read_pdf(file_path):
    reader = PdfReader(file_path) 
    text = ""
    for page_num in range(len(reader.pages)):
        page = reader.pages[page_num]
        newText = page.extract_text()
        text += newText.replace('\n', ' ')   
    return text

def read_file_content(file_path):
    if file_path.endswith('.docx'):
        return read_docx(file_path)
    elif file_path.endswith('.pdf'):
        return read_pdf(file_path)
    else:
        raise ValueError("Unsupported file format. Please use .docx or .pdf files.")

def getCanvasAPI(url):  
    params = {'per_page': 10000}
    response = requests.get(url, headers=headers)
    results = []
    
    while url and len(results) < 20:
        response = requests.get(url, headers=headers, params=params)
        if response.status_code != 200:
            raise Exception(f"API request failed with status code {response.status_code}")
        results.extend(response.json())
        url = response.links.get('next', {}).get('url')
    
    if response.status_code == 200:
        return results
    else:
        print(f"API request failed with status code {response.status_code}")
        return None

def getUserList(crn):
    url = API_URL + '/courses/' + crn + '/users'
    students = getCanvasAPI(url)
    
    id_to_name = {student['id']: student['name'] for student in students}
    directory = './submissions/'
    name_file_pairs = []
    files = os.listdir(directory)
    pattern = re.compile(r'_(\d+)_')

    for filename in files:
        match = pattern.search(filename)
        if match:
            student_id = int(match.group(1))
            if student_id in id_to_name:
                name_file_pairs.append((id_to_name[student_id], filename))

    for name, filename in name_file_pairs:
        print(f'Name: {name}, Filename: {filename}')
    return name_file_pairs

def parse_student_name(name_part):
    """
    Parse student name from filename part like 'barronjohn' -> 'John Barron'
    Simple approach with common name detection
    """
    name_lower = name_part.lower()
    
    # Common first names that might appear at the end
    common_first_names = [
        'john', 'jane', 'mary', 'mike', 'dave', 'tom', 'bob', 'jim', 'joe', 
        'ann', 'sue', 'amy', 'dan', 'sam', 'kim', 'pat', 'chris', 'steve', 
        'karen', 'linda', 'nancy', 'betty', 'helen', 'maria', 'lisa',
        'robert', 'william', 'michael', 'jennifer', 'matthew', 'andrew',
        'joshua', 'daniel', 'james', 'david', 'sarah', 'jessica', 'ashley',
        'emily', 'amanda', 'melissa', 'nicole', 'stephanie', 'elizabeth',
        'christian', 'christopher', 'alexander', 'jonathan', 'nicholas',
        'anthony', 'benjamin', 'zachary', 'samantha', 'brittany', 'lauren',
        'megan', 'rachel', 'kimberly', 'christina', 'katherine', 'danielle',
        'anahi', 'jenna', 'henry', 'barbara', 'hunter', 'tanisha', 'veronica',
        'simon', 'ashlyn', 'cierra', 'ysabelle', 'marcela', 'james'
    ]
    
    # Try to find a first name at the end
    for first_name in common_first_names:
        if name_lower.endswith(first_name):
            # Split at the first name
            lastname = name_part[:-len(first_name)]
            firstname = name_part[-len(first_name):]
            if len(lastname) >= 2:  # Make sure we have a reasonable lastname
                return f"{firstname.title()} {lastname.title()}"
    
    # If no common name found, try to split roughly in the middle
    if len(name_part) >= 6:  # Only try this for reasonably long names
        mid = len(name_part) // 2
        # Try a few positions around the middle
        for offset in [-1, 0, 1, -2, 2]:
            split_pos = mid + offset
            if 2 <= split_pos <= len(name_part) - 2:  # Ensure both parts are at least 2 chars
                lastname = name_part[:split_pos]
                firstname = name_part[split_pos:]
                return f"{firstname.title()} {lastname.title()}"
    
    # Fallback: just capitalize and return as-is
    return name_part.title()


# Quick fix for the immediate error in grade_all.py
# Replace the existing makeEntryList function with this version:

def makeEntryList(crn):
    """Create entry list with proper Canvas name lookup and robust error handling"""
    entryList = []
    userList = []
    gotUsers = False
    directory = "./submissions/"
    
    print(f"DEBUG: Looking for submissions in: {directory}")
    print(f"DEBUG: Using CRN: {crn}")
    
    try:
        files_found = os.listdir(directory)
        print(f"DEBUG: Found {len(files_found)} files")
    except FileNotFoundError:
        print(f"ERROR: Directory {directory} not found")
        return entryList
    
    for filename in os.listdir(directory):
        filepath = os.path.join(directory, filename)
        print(f"DEBUG: Processing file: {filename}")
        
        if filename.endswith('.html'):
            # FIXED HTML processing with error handling
            thisEntry = []
            try:
                fileObj = open(filepath, 'r', encoding='utf-8')
                soup = BeautifulSoup(fileObj, 'html.parser')
                fileObj.close()
                
                # FIXED: Safe extraction of student name
                student_name = "Unknown Student"
                author_span = soup.find('span', class_='author_name')
                if author_span:
                    student_name = author_span.get_text()
                else:
                    # Try alternative patterns
                    for pattern in [
                        ('div', {'class': 'author'}),
                        ('span', {'class': 'student_name'}),
                        ('h1', {}),
                        ('h2', {})
                    ]:
                        element = soup.find(pattern[0], pattern[1])
                        if element:
                            potential_name = element.get_text(strip=True)
                            if potential_name:
                                student_name = potential_name
                                break
                    
                    # If still no name found, extract from filename
                    if student_name == "Unknown Student":
                        base_name = os.path.splitext(filename)[0]
                        student_name = base_name.replace('_', ' ').title()
                
                # FIXED: Safe extraction of submission content
                submission_content = ""
                content_div = soup.find('div', class_='show_message_content')
                if content_div:
                    submission_content = content_div.get_text()
                else:
                    # Try alternative patterns
                    for pattern in [
                        ('div', {'class': 'content'}),
                        ('div', {'class': 'message_content'}),
                        ('main', {}),
                        ('article', {})
                    ]:
                        element = soup.find(pattern[0], pattern[1])
                        if element:
                            potential_content = element.get_text(strip=True)
                            if len(potential_content) > 10:  # Ensure substantial content
                                submission_content = potential_content
                                break
                    
                    # Final fallback: get all text
                    if not submission_content:
                        all_text = soup.get_text(strip=True)
                        if all_text:
                            # Remove the first line (likely the name) and use the rest
                            lines = all_text.split('\n')
                            if len(lines) > 1:
                                submission_content = '\n'.join(lines[1:]).strip()
                            else:
                                submission_content = all_text
                
                # Only add entry if we have content
                if submission_content.strip():
                    thisEntry.append(student_name)
                    thisEntry.append(submission_content)
                    thisEntry.append(None)
                    entryList.append(thisEntry)
                    print(f"DEBUG: Added HTML entry for {student_name}")
                else:
                    print(f"WARNING: No content found in {filename}")
                    
            except Exception as e:
                print(f"ERROR: Could not process HTML file {filename}: {e}")
                # Add an error entry so we don't lose track of the submission
                error_name = os.path.splitext(filename)[0].replace('_', ' ').title()
                thisEntry = [error_name, f"Error processing HTML file: {str(e)}", None]
                entryList.append(thisEntry)
            
        elif filename.endswith(('.pdf', '.docx')):
            # PDF/DOCX processing with Canvas name lookup (unchanged)
            if not gotUsers:
                print(f"DEBUG: Getting user list from Canvas for CRN {crn}")
                try:
                    userList = getUserList(crn)
                    gotUsers = True
                    print(f"DEBUG: Got {len(userList)} users from Canvas")
                except Exception as e:
                    print(f"WARNING: Could not get Canvas user list: {e}")
                    userList = []
                    gotUsers = True
            
            thisEntry = []
            
            # Extract student ID from filename
            filename_parts = filename.split('_')
            student_id = None
            student_name = "Unknown Student"
            
            # Look for student ID (6+ digit number) in filename parts
            for i, part in enumerate(filename_parts):
                if part == 'LATE':
                    continue
                if part.isdigit() and len(part) >= 6:
                    student_id = part
                    print(f"DEBUG: Found student ID: {student_id}")
                    break
            
            # Look up proper name in Canvas user list
            if student_id and userList:
                for canvas_name, canvas_info in userList:
                    if student_id in str(canvas_info):
                        student_name = canvas_name
                        print(f"DEBUG: Matched Canvas name: {student_name}")
                        break
                else:
                    print(f"DEBUG: No Canvas match found for ID {student_id}")
                    name_part = filename_parts[0] if filename_parts else filename.split('.')[0]
                    student_name = parse_filename_name(name_part)
            else:
                print(f"DEBUG: No student ID found, using filename parsing")
                name_part = filename_parts[0] if filename_parts else filename.split('.')[0] 
                student_name = parse_filename_name(name_part)
            
            # Read file content
            try:
                data = read_file_content(filepath)
                if data and data.strip():
                    thisEntry.append(student_name)
                    thisEntry.append(data)
                    thisEntry.append(None)
                    entryList.append(thisEntry)
                    print(f"DEBUG: Added entry for {student_name}")
                else:
                    print(f"ERROR: No content extracted from {filename}")
                    # Add an error entry
                    thisEntry = [student_name, f"No content could be extracted from {filename}", None]
                    entryList.append(thisEntry)
            except Exception as e:
                print(f"ERROR: Could not read {filename}: {e}")
                # Add an error entry
                thisEntry = [student_name, f"Error reading file {filename}: {str(e)}", None]
                entryList.append(thisEntry)
    
    print(f"DEBUG: Total entries created: {len(entryList)}")
    return entryList

def parse_filename_name(name_part):
    """Parse student name from filename when Canvas lookup fails"""
    # Simple fallback parsing
    common_first_names = [
        'john', 'jane', 'michael', 'sarah', 'david', 'jennifer', 'james', 'jessica',
        'robert', 'ashley', 'william', 'amanda', 'richard', 'melissa', 'joseph',
        'michelle', 'thomas', 'kimberly', 'christopher', 'amy', 'daniel', 'angela',
        'matthew', 'helen', 'anthony', 'deborah', 'mark', 'rachel', 'donald',
        'carolyn', 'steven', 'janet', 'paul', 'catherine', 'andrew', 'maria',
        'joshua', 'heather', 'kenneth', 'diane', 'kevin', 'ruth', 'brian',
        'julie', 'george', 'joyce', 'edward', 'virginia', 'ronald', 'victoria',
        'christian', 'henry', 'jovany', 'anahi', 'tanisha', 'barbara', 'hunter',
        'veronica', 'samantha', 'simon', 'jenna', 'ashlyn', 'brittny', 'marcela',
        'cierra', 'ysabelle', 'james'
    ]
    
    name_lower = name_part.lower()
    
    # Look for common first names at the end
    for first_name in common_first_names:
        if name_lower.endswith(first_name):
            last_name = name_lower[:-len(first_name)]
            if last_name:  # Make sure there's a last name part
                return f"{first_name.title()} {last_name.title()}"
    
    # Fallback: try to split at reasonable points
    if len(name_lower) > 6:
        mid_point = len(name_lower) // 2
        # Look for vowels near the middle to split on
        vowels = 'aeiou'
        for offset in range(-2, 3):
            split_point = mid_point + offset
            if 0 < split_point < len(name_lower) - 1:
                if name_lower[split_point] in vowels:
                    first_part = name_lower[:split_point+1]
                    second_part = name_lower[split_point+1:]
                    return f"{second_part.title()} {first_part.title()}"
    
    # Final fallback: just return the name as-is, capitalized
    return name_part.title()

def split_into_many(tokenizer, text, max_tokens=max_tokens):
    sentences = text.split('. ')
    n_tokens = [len(tokenizer.encode(" " + sentence)) for sentence in sentences]
    
    chunks = []
    tokens_so_far = 0
    chunk = []

    for sentence, token in zip(sentences, n_tokens):
        if tokens_so_far + token > max_tokens:
            chunks.append(". ".join(chunk) + ".")
            chunk = []
            tokens_so_far = 0

        if token > max_tokens:
            continue

        chunk.append(sentence)
        tokens_so_far += token + 1
        
    if chunk:
        chunks.append(". ".join(chunk) + ".")

    return chunks

def remove_newlines(serie):
    serie = serie.str.replace('\n', ' ')
    serie = serie.str.replace('\\n', ' ')
    serie = serie.str.replace('  ', ' ')
    serie = serie.str.replace('  ', ' ')
    return serie

def createDataframe():
    """Modified to use local embeddings instead of OpenAI"""
    global shortened
    global df
    global df_embeddings
    global df_similarities

    df = pd.DataFrame(lecStrList, columns=['fname', 'text', 'id'])
    df.to_csv('embeddings.csv')
    
    tokenizer = tiktoken.get_encoding("cl100k_base")
    df = pd.read_csv('embeddings.csv', index_col=0)
    df.columns = ['title', 'text', 'id']
    
    df['n_tokens'] = df.text.apply(lambda x: len(tokenizer.encode(x)) if x is not None else 0)
    
    for row in df.iterrows():
        if row[1].text is None:
            continue
            
        if row[1].n_tokens > max_tokens:
            shortened += split_into_many(tokenizer, row[1].text)
        else:
            shortened.append(row[1].text)

    df = pd.DataFrame(shortened, columns=['text'])
    df['n_tokens'] = df.text.apply(lambda x: len(tokenizer.encode(x)))
    
    print("Generating embeddings using local model...")
    df['embeddings'] = df.text.apply(lambda x: get_embedding(x))
    
    df.to_csv('tokens.csv')
    print("Embeddings generated and saved!")

def sort_by_number(string_list):
    def get_number(text):
        return int(text.split(':')[-1])
    return sorted(string_list, key=get_number)

def crawl():
    global qFileList109, qFileList110, aFileList109, aFileList110
    global lecStrList, dscStrList, US1List, US2List, US1DList, US2DList

    dirTree = []

    for (root, dirs, files) in os.walk(domain, topdown=True): 
        rootcrs = root.find("109")
        if rootcrs >= 0:
            rootdir = 109
        rootcrs = root.find("110")
        if rootcrs >= 0:
            rootdir = 110
        dirTree.append(dirs)      
        
        while files:
            foundQ = False
            foundA = False
            fileList = files.pop(0)
            x = fileList.find("q.txt")
            if x >= 0:
                newFile = "./" + root + "/" + fileList
                f = open(newFile)
                qinFileList = f.readlines()
                f.close()
                if rootdir == 109:
                    qFileList109.append(qinFileList)
                elif rootdir == 110:
                    qFileList110.append(qinFileList)
                foundQ = True
                
            x = fileList.find("a.dsc")
            if x >= 0:
                newFile = "./" + root + "/" + fileList
                f = open(newFile)
                qinFileList = f.readlines()
                f.close()
                if rootdir == 109:
                    aFileList109.append(qinFileList)
                elif rootdir == 110:
                    qFileList110.append(qinFileList)
                foundA = True
                
            x = fileList.find(".txt")
            if (x >= 0) and (foundQ == False) and (foundA == False):
                newFile = "./" + root + "/" + fileList
                titleList = [newFile]
                f = open(newFile, "r")
                inputList = f.readlines()
                f.close()
                lStr = inputList[2]
                l1Str = inputList[1].replace("\n", "")
                lnStr = l1Str + lStr[:3]
                idnumstr = lStr[:3]
                x = inputList[1].find("US1")
                if x >= 0:                    
                    US1List.append(lnStr)
                else:
                    US2List.append(lnStr)

                removeStr = (inputList[1].replace("\n", "") + inputList[2].replace("\n", ""))
                idStr = inputList[2].replace("\n", "")
                idStr1 = idStr.replace("-", "")
                idStr2 = idStr1.replace(" ", "")
                id = int(idStr2)
                titleList.append(id)
                titleStr = removeStr + inputList[3]
                lecStr = inputList[4].replace("\n", "")
                tlecStrList = []
                tlecStrList.append(titleStr)
                tlecStrList.append(lecStr)
                tlecStrList.append(idnumstr)
                lecStrList.append(tlecStrList)
                
            x = fileList.find(".dsc")
            if (x >= 0) and (foundA == False):
                newFile = "./" + root + "/" + fileList
                titleList = [newFile]
                f = open(newFile, "r")
                inputList = f.readlines()
                f.close()
                lStr = inputList[2]
                l1Str = inputList[1].replace("\n", "")
                lnStr = l1Str + lStr[:3]
                idnumstr = lStr[:3]
                x = inputList[1].find("US1")
                if x >= 0:                    
                    US1DList.append(lnStr)
                else:
                    US2DList.append(lnStr)

                removeStr = (inputList[1].replace("\n", "") + inputList[2].replace("\n", ""))
                idStr = inputList[2].replace("\n", "")
                idStr1 = idStr.replace("-", "")
                idStr2 = idStr1.replace(" ", "")
                id = int(idStr2)
                titleList.append(id)
                titleStr = removeStr + inputList[3]
                lecStr = inputList[4].replace("\n", "")
                tlecStrList = []
                tlecStrList.append(titleStr)
                tlecStrList.append(lecStr)
                tlecStrList.append(idnumstr)
                dscStrList.append(tlecStrList)    

    sorted_list = sorted(lecStrList, key=itemgetter(2))
    lecStrList = sorted_list
    sorted_list = sorted(qFileList109, key=itemgetter(0))
    qFileList109 = sorted_list
    sorted_list = sorted(qFileList110, key=itemgetter(0))
    qFileList110 = sorted_list
    sorted_list = sorted(dscStrList, key=itemgetter(2))
    dscStrList = sorted_list
    sorted_list = sorted(aFileList109, key=itemgetter(0))
    aFileList109 = sorted_list
    sorted_list = sorted(aFileList110, key=itemgetter(0))
    aFileList110 = sorted_list

    sorted_list = sort_by_number(US1List)
    US1List = sorted_list
    sorted_list = sort_by_number(US2List)
    US2List = sorted_list
    sorted_list = sort_by_number(US1DList)
    US1DList = sorted_list
    sorted_list = sort_by_number(US2DList)
    US2DList = sorted_list
    createDataframe()

# Original prompt functions (kept for backward compatibility)
def getDiscussionPromptStr(course, base):
    print("Begin discussion completions...")
    filename = full_url + "dscquery.prmt"
    f = open(filename)
    promptList = f.readlines()
    f.close()
    prompt1 = promptList[0].replace("\n", " ")
    prompt2 = promptList[1].replace("\n", " ")

    if course == 111:
        courseStr = "US1"
    else:
        courseStr = "US2"
    itemStr = "{:03}".format(base)
    dscStr = ""
    for sublist in dscStrList:
        x = sublist[0].find(courseStr)
        if x >= 0:
            y = sublist[0].find(itemStr)
            if y >= 0:
                dscStr = sublist[1].replace("\n", " ")
                break

    baseStr = str(base)
    if course == 111:
        qList = aFileList109
    else:
        qList = aFileList110

    itemStr = "{:03}".format(base)
    aStr = ""
    for sublist in qList:
        x = sublist[0].find(itemStr)
        if x >= 0:
            listlen = len(sublist)
            y = 2
            while y < listlen:
                nxtStr = sublist[y].replace("\n", " ")
                newStr = aStr + nxtStr
                aStr = newStr
                y = y + 1

    promptStr = prompt1 + " " + dscStr + " " + prompt2 + " " + aStr + "\n" 
    return promptStr

def getReviewPromptStr():
    print("Begin review completions...\n")
    filename = full_url + "rvwquery.prmt"
    f = open(filename)
    promptList = f.readlines()
    f.close()
    return promptList[0]

def getPromptStr(course, base):
    print("Begin grading completions...\n")

    filename = full_url + "query.prmt"
    f = open(filename)
    promptList = f.readlines()
    f.close()
    prompt1 = promptList[0].replace("\n", " ")
    prompt2 = promptList[1].replace("\n", " ")

    if course == 109:
        courseStr = "US1"
    else:
        courseStr = "US2"
    itemStr = "{:03}".format(base)
    lecStr = ""
    for sublist in lecStrList:
        x = sublist[0].find(courseStr)
        if x >= 0:
            y = sublist[0].find(itemStr)
            if y >= 0:
                lecStr = sublist[1].replace("\n", " ")
                break

    baseStr = str(base)
    if course == 109:
        qList = qFileList109
    else:
        qList = qFileList110

    qStr = ""
    for sublist in qList:
        x = sublist[0].find(itemStr)
        if x >= 0:
            listlen = len(sublist)
            y = 2
            while y < listlen:
                nxtStr = sublist[y].replace("\n", " ")
                qStr = qStr + nxtStr
                y = y + 1

    promptStr = prompt1 + " " + lecStr + " " + prompt2 + " " + qStr + "\n" 
    return promptStr

# New helper functions for batch processing
def getLectureContent(course, base):
    """Extract just the lecture content without prompt formatting"""
    if course == 109:
        courseStr = "US1"
    else:
        courseStr = "US2"
        
    itemStr = "{:03}".format(base)
    lecStr = ""
    
    for sublist in lecStrList:
        x = sublist[0].find(courseStr)
        if x >= 0:
            y = sublist[0].find(itemStr)
            if y >= 0:
                lecStr = sublist[1].replace("\n", " ")
                break
    
    return lecStr

def getQuestions(course, base):
    """Extract just the questions without prompt formatting"""
    baseStr = str(base)
    if course == 109:
        qList = qFileList109
    else:
        qList = qFileList110

    itemStr = "{:03}".format(base)
    qStr = ""
    
    for sublist in qList:
        x = sublist[0].find(itemStr)
        if x >= 0:
            listlen = len(sublist)
            y = 2
            while y < listlen:
                nxtStr = sublist[y].replace("\n", " ")
                qStr = qStr + nxtStr
                y = y + 1
    
    return qStr

def getDiscussionLectureContent(course, base):
    """Extract discussion lecture content"""
    if course == 111:
        courseStr = "US1"
    else:
        courseStr = "US2"
        
    itemStr = "{:03}".format(base)
    dscStr = ""
    
    for sublist in dscStrList:
        x = sublist[0].find(courseStr)
        if x >= 0:
            y = sublist[0].find(itemStr)
            if y >= 0:
                dscStr = sublist[1].replace("\n", " ")
                break
    
    return dscStr

def getDiscussionQuestions(course, base):
    """Extract discussion questions"""
    baseStr = str(base)
    if course == 111:
        qList = aFileList109
    else:
        qList = aFileList110

    itemStr = "{:03}".format(base)
    aStr = ""
    
    for sublist in qList:
        x = sublist[0].find(itemStr)
        if x >= 0:
            listlen = len(sublist)
            y = 2
            while y < listlen:
                nxtStr = sublist[y].replace("\n", " ")
                newStr = aStr + nxtStr
                aStr = newStr
                y = y + 1

    return aStr

def clean_feedback_comprehensive(feedback_text):
    """
    Comprehensive function to clean greetings and closings from any feedback text.
    This works regardless of where the feedback comes from (individual, batch, or reviews).
    """
    if not feedback_text or not isinstance(feedback_text, str):
        return feedback_text
    
    import re
    
    # Remove various greeting patterns (more comprehensive)
    greeting_patterns = [
        r'^Dear\s+[^,:\n]*[,:]?\s*',           # Dear Student, Dear John,
        r'^Hello\s+[^,:\n]*[,:]?\s*',          # Hello Student,
        r'^Hi\s+[^,:\n]*[,:]?\s*',             # Hi there,
        r'^Greetings\s*[,:]?\s*',              # Greetings,
        r'^Good\s+\w+\s*[,:]?\s*',             # Good morning,
        r'^\w+\s*,\s*',                        # Student, or Name,
        r'^To\s+[^,:\n]*[,:]?\s*',             # To the student,
    ]
    
    for pattern in greeting_patterns:
        feedback_text = re.sub(pattern, '', feedback_text, flags=re.IGNORECASE | re.MULTILINE)
    
    # Remove closing patterns
    closing_patterns = [
        r'Best\s+regards.*$',
        r'Sincerely.*$', 
        r'Best\s+wishes.*$',
        r'Thank\s+you.*$',
        r'Yours\s+truly.*$',
        r'\[Professor[^\]]*\].*$',
        r'Dr\.\s+\w+.*$',
        r'Professor\s+\w+.*$',
    ]
    
    for pattern in closing_patterns:
        feedback_text = re.sub(pattern, '', feedback_text, flags=re.IGNORECASE | re.MULTILINE)
    
    # Clean up extra whitespace and newlines
    feedback_text = re.sub(r'\n\s*\n', '\n', feedback_text)  # Remove empty lines
    feedback_text = re.sub(r'^\s+', '', feedback_text)        # Remove leading whitespace
    feedback_text = re.sub(r'\s+$', '', feedback_text)        # Remove trailing whitespace
    feedback_text = re.sub(r'\s+', ' ', feedback_text)        # Normalize internal whitespace
    
    # Ensure first letter is capitalized if there's content
    if feedback_text:
        feedback_text = feedback_text[0].upper() + feedback_text[1:] if len(feedback_text) > 1 else feedback_text.upper()
    
    return feedback_text


def process_review_feedback(raw_feedback):
    """
    Special processing for review feedback that might come from getReviewPromptStr()
    """
    # First apply the comprehensive cleaning
    cleaned = clean_feedback_comprehensive(raw_feedback)
    
    # Additional review-specific cleaning
    import re
    
    # Remove any remaining review-specific patterns
    review_patterns = [
        r'^This\s+review\s*[,:]?\s*',          # "This review:"
        r'^Review\s*[,:]?\s*',                 # "Review:"
        r'^Feedback\s*[,:]?\s*',               # "Feedback:"
        r'^Comments\s*[,:]?\s*',               # "Comments:"
    ]
    
    for pattern in review_patterns:
        cleaned = re.sub(pattern, '', cleaned, flags=re.IGNORECASE)
    
    # Final cleanup
    cleaned = cleaned.strip()
    
    return cleaned


# Updated CSV writing function that cleans ALL feedback
def write_results_to_csv_with_cleaning(results, scoring_scale, processing_method):
    """Write grading results to CSV with comprehensive feedback cleaning"""
    try:
        import os
        import csv
        
        os.makedirs('./submissions', exist_ok=True)
        csv_filename = './submissions/completions.csv'
        
        with open(csv_filename, 'w', newline='', encoding='utf-8') as file:
            writer = csv.writer(file)
            
            # Always write headers first
            writer.writerow(['Student Name', 'Submission', 'Grade and Feedback'])
            
            # Write data rows with cleaned feedback
            for result in results:
                if isinstance(result, dict):
                    # From individual/batch grader (new format)
                    # Clean the feedback before formatting
                    clean_feedback_text = clean_feedback_comprehensive(result['feedback'])
                    formatted_feedback = f"{result['score']}/{result['max_score']} ({result['letter_grade']}) - {clean_feedback_text}"
                    
                    writer.writerow([
                        result['student_name'],
                        'Submission content',
                        formatted_feedback
                    ])
                else:
                    # From old format (3-element list) - likely from Reviews
                    student_name = result[0]
                    submission = result[1] 
                    raw_feedback = result[2] if result[2] else ""
                    
                    # Clean the feedback
                    cleaned_feedback = process_review_feedback(raw_feedback)
                    
                    writer.writerow([
                        student_name,
                        submission,
                        cleaned_feedback
                    ])
        
        print(f"✅ Results saved to {csv_filename} with cleaned feedback")
        return csv_filename
        
    except Exception as e:
        print(f"❌ Error writing CSV: {e}")
        return None


def main():
    import sys    
    crawl()
    makeEntryList('2486450')  # Default CRN
    print("done")

if __name__ == "__main__":
    main()