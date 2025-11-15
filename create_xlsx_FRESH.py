#!/usr/bin/env python3
"""
FRESH VERSION - Create XLSX from CSV completions file
This version has VERY visible logging to diagnose the issue
"""
import csv
import sys
import re
from pathlib import Path
import pandas as pd
from bs4 import BeautifulSoup

print("\n" + "="*70)
print("RUNNING FRESH VERSION OF CREATE_XLSX")
print("="*70 + "\n")

try:
    from canvas_api import get_course_students
    CANVAS_API_AVAILABLE = True
except ImportError:
    CANVAS_API_AVAILABLE = False

def _clean_html(html_text):
    """Extract plain text from HTML."""
    print(f"    _clean_html called with {len(str(html_text))} chars")
    if not html_text or pd.isna(html_text):
        print(f"    _clean_html returning None (empty input)")
        return None
    soup = BeautifulSoup(str(html_text), 'html.parser')
    for script_or_style in soup(['script', 'style']):
        script_or_style.decompose()
    text = soup.get_text(separator='\n')
    lines = (line.strip() for line in text.splitlines())
    chunks = (line for line in lines if line)
    result = '\n'.join(chunks)
    print(f"    _clean_html returning {len(result)} chars")
    return result

def _strip_url_prefix(text):
    """Remove Canvas URL prefix if present."""
    print(f"    _strip_url_prefix called with: {repr(text)[:100]}")
    if not text or pd.isna(text):
        print(f"    _strip_url_prefix returning empty (no input)")
        return ""
    text = str(text).strip()
    url_pattern = r'^https?://[^\s]+\n\n'
    cleaned = re.sub(url_pattern, '', text, count=1)
    result = cleaned.strip()
    print(f"    _strip_url_prefix returning: {repr(result)[:100]}")
    return result

def parse_name_from_label(label):
    """Parse name from label like 'mirandajake_8872823'."""
    name_part = label.split('_')[0] if '_' in label else label
    name_part = re.sub(r'[^a-zA-Z]', '', name_part).lower()
    mid = len(name_part) // 2
    last_name = name_part[:mid].capitalize()
    first_name = name_part[mid:].capitalize()
    return first_name, last_name

def main():
    if len(sys.argv) < 2:
        print("Usage: python3 create_xlsx_FRESH.py <csv_file> [course_id] [canvas_url] [token]")
        sys.exit(1)
    
    csv_file = sys.argv[1]
    print(f"CSV FILE: {csv_file}\n")
    
    # Get roster
    id_to_names = {}
    if len(sys.argv) >= 5 and CANVAS_API_AVAILABLE:
        course_id = sys.argv[2]
        canvas_url = sys.argv[3]
        token = sys.argv[4]
        print(f"Fetching Canvas roster...")
        try:
            students = get_course_students(canvas_url, course_id, token)
            for s in students:
                uid = str(s.get('id', ''))
                name = s.get('sortable_name', '')
                if name and ',' in name:
                    last, first = [n.strip() for n in name.split(',', 1)]
                    id_to_names[uid] = (last, first)
            print(f"✓ Roster loaded: {len(id_to_names)} students\n")
        except Exception as e:
            print(f"⚠️  Roster fetch failed: {e}\n")
    
    # Read CSV
    print(f"Reading CSV...")
    with open(csv_file, 'r', encoding='utf-8') as f:
        reader = csv.reader(f)
        rows = list(reader)
    print(f"✓ CSV has {len(rows)} rows (including header)\n")
    
    # Process rows
    user_ids = []
    last_names = []
    first_names = []
    submission_texts = []
    scores = []
    uploads = []
    
    skipped = 0
    
    for idx, row in enumerate(rows[1:], start=1):
        print(f"\n{'─'*70}")
        print(f"ROW {idx}:")
        
        if len(row) < 2:
            print(f"  ⚠️  Too few columns, skipping")
            skipped += 1
            continue
        
        html_text = row[0] if len(row) > 0 else ""
        label = row[1] if len(row) > 1 else ""
        score = row[2] if len(row) > 2 else ""
        
        print(f"  Label: {label}")
        print(f"  Score: {score}")
        print(f"  HTML: {len(html_text)} chars")
        
        # Get UID
        uid_match = re.search(r'_(\d+)', label)
        if not uid_match:
            print(f"  ⚠️  No UID found, skipping")
            skipped += 1
            continue
        uid = uid_match.group(1)
        print(f"  UID: {uid}")
        
        # Get name
        if uid in id_to_names:
            last, first = id_to_names[uid]
            print(f"  Name from roster: {first} {last}")
        else:
            if id_to_names:
                print(f"  ⚠️  Not in roster (inactive?), skipping")
                skipped += 1
                continue
            else:
                first, last = parse_name_from_label(label)
                print(f"  Name from label: {first} {last}")
        
        # Process text
        print(f"  Processing submission text...")
        cleaned = _clean_html(html_text)
        final_text = _strip_url_prefix(cleaned) if cleaned else ""
        
        if final_text:
            print(f"  ✓ Final text: {len(final_text)} chars")
        else:
            print(f"  ⚠️  Final text is EMPTY")
        
        # Append to lists
        print(f"  Appending to lists...")
        user_ids.append(uid)
        last_names.append(last)
        first_names.append(first)
        submission_texts.append(final_text if final_text else None)
        scores.append(score)
        uploads.append("yes")
        
        print(f"  ✓ Added to lists (submission_text = {repr(final_text)[:50] if final_text else 'None'})")
    
    print(f"\n{'='*70}")
    print(f"SUMMARY:")
    print(f"  Total rows in CSV: {len(rows)-1}")
    print(f"  Rows processed: {len(user_ids)}")
    print(f"  Rows skipped: {skipped}")
    print(f"  Non-empty texts: {sum(1 for t in submission_texts if t)}")
    print(f"{'='*70}\n")
    
    # Create DataFrame
    print("Creating DataFrame...")
    df = pd.DataFrame({
        'Canvas User ID': user_ids,
        'Student Last Name': last_names,
        'Student First Name': first_names,
        'Student Submission Text': submission_texts,
        'Grade': scores,
        'Upload?': uploads
    })
    
    print(f"DataFrame shape: {df.shape}")
    print(f"Submission text column - notna: {df['Student Submission Text'].notna().sum()}")
    print(f"Submission text column - isna: {df['Student Submission Text'].isna().sum()}")
    
    # Save
    output_file = csv_file.replace('.csv', '.xlsx')
    if output_file == csv_file:
        output_file = csv_file + '.xlsx'
    
    print(f"\nSaving to: {output_file}")
    df.to_excel(output_file, index=False)
    print(f"✓ DONE!\n")

if __name__ == '__main__':
    main()
