#!/usr/bin/env python3
"""
Create XLSX from CSV completions file for Canvas upload
"""
import csv
import re
import pandas as pd
from bs4 import BeautifulSoup

def _clean_html(html_text):
    """Extract plain text from HTML, preserving some structure."""
    if not html_text or pd.isna(html_text):
        return None
    soup = BeautifulSoup(str(html_text), 'html.parser')
    for script_or_style in soup(['script', 'style']):
        script_or_style.decompose()
    text = soup.get_text(separator='\n')
    lines = (line.strip() for line in text.splitlines())
    chunks = (line for line in lines if line)
    return '\n'.join(chunks)

def _strip_url_prefix(text):
    """Remove Canvas URL from the beginning if present."""
    if not text or pd.isna(text):
        return ""
    text = str(text).strip()
    # Remove URL at the start - it may be followed by one or more newlines
    url_pattern = r'^https?://[^\s]+\s*'
    cleaned = re.sub(url_pattern, '', text, count=1)
    return cleaned.strip()

def parse_name_from_label(label):
    """Parse first and last name from label like 'mirandajake_8872823_text'."""
    name_part = label.split('_')[0] if '_' in label else label
    name_part = re.sub(r'[^a-zA-Z]', '', name_part).lower()
    
    # Simple heuristic: split roughly in middle
    mid = len(name_part) // 2
    last_name = name_part[:mid].capitalize()
    first_name = name_part[mid:].capitalize()
    
    return first_name, last_name

def create_xlsx(csv_file, output_file=None, course_id=None, assignment_id=None, 
                canvas_url=None, token=None, id_to_names=None):
    """
    Create XLSX from CSV completions file.
    
    Args:
        csv_file: Path to CSV file
        output_file: Optional output path (defaults to replacing .csv with .xlsx)
        course_id: Canvas course ID (for fetching roster)
        assignment_id: Canvas assignment ID (not currently used, for compatibility)
        canvas_url: Canvas base URL (for fetching roster)
        token: Canvas API token (for fetching roster)
        id_to_names: Optional dict mapping user_id -> (last_name, first_name)
                     If not provided and Canvas credentials given, will fetch from Canvas
    
    Returns:
        Path to created XLSX file
    """
    # Fetch roster if credentials provided and no roster dict given
    if id_to_names is None:
        id_to_names = {}
        
        if course_id and canvas_url and token:
            try:
                from canvas_api import get_course_students
                students = get_course_students(canvas_url, course_id, token)
                for s in students:
                    uid = str(s.get('id', ''))
                    name = s.get('sortable_name', '')
                    if name and ',' in name:
                        last, first = [n.strip() for n in name.split(',', 1)]
                        id_to_names[uid] = (last, first)
            except Exception as e:
                print(f"Warning: Could not fetch Canvas roster: {e}")
    
    # Read CSV
    with open(csv_file, 'r', encoding='utf-8') as f:
        reader = csv.reader(f)
        rows = list(reader)
    
    if len(rows) < 2:
        raise ValueError("CSV file is empty or has no data rows")
    
    # Process rows
    user_ids = []
    last_names = []
    first_names = []
    submission_texts = []
    scores = []
    uploads = []
    
    skipped_inactive = 0
    processed = 0
    
    for row in rows[1:]:  # Skip header
        if len(row) < 2:
            continue
        
        # CSV format: [label, HTML, score, feedback, ...]
        label = row[0] if len(row) > 0 else ""           # Column 0: label
        html_text = row[1] if len(row) > 1 else ""       # Column 1: HTML content
        score = row[2] if len(row) > 2 else ""           # Column 2: score
        
        # Get user ID from label
        uid_match = re.search(r'_(\d+)', label)
        if not uid_match:
            continue
        uid = uid_match.group(1)
        
        # Try to get name from roster first
        if uid in id_to_names:
            last, first = id_to_names[uid]
        else:
            # Not in roster - could be inactive/dropped
            if id_to_names:  # Only skip if we actually have a roster to compare against
                skipped_inactive += 1
                continue
            else:
                # No roster available, parse from label
                first, last = parse_name_from_label(label)
        
        # Clean HTML and extract text
        cleaned = _clean_html(html_text)
        final_text = _strip_url_prefix(cleaned) if cleaned else ""
        
        # Add to lists (use None instead of empty string to avoid Excel issues)
        user_ids.append(uid)
        last_names.append(last)
        first_names.append(first)
        submission_texts.append(final_text if final_text else None)
        scores.append(score)
        uploads.append("yes")
        processed += 1
    
    print(f"Processed {processed} submissions from active roster")
    if skipped_inactive > 0:
        print(f"Skipped {skipped_inactive} inactive/dropped students")
    print(f"Found names for {sum(1 for f in first_names if f)} students")
    
    # Create DataFrame
    df = pd.DataFrame({
        'Canvas User ID': user_ids,
        'Student Last Name': last_names,
        'Student First Name': first_names,
        'Student Submission Text': submission_texts,
        'Grade': scores,
        'Upload?': uploads
    })
    
    # Generate output filename if not provided
    if output_file is None:
        output_file = csv_file.replace('.csv', '.xlsx')
        if output_file == csv_file:
            output_file = csv_file + '.xlsx'
    
    # Save
    df.to_excel(output_file, index=False)
    print(f"\n✓ Created XLSX: {output_file}")
    print(f"  Rows: {len(df)}")
    print(f"  Non-empty submission texts: {df['Student Submission Text'].notna().sum()}")
    
    return output_file

def main():
    """Command-line interface."""
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: python3 create_xlsx.py <csv_file> [course_id] [canvas_url] [token]")
        sys.exit(1)
    
    csv_file = sys.argv[1]
    course_id = sys.argv[2] if len(sys.argv) > 2 else None
    canvas_url = sys.argv[3] if len(sys.argv) > 3 else None
    token = sys.argv[4] if len(sys.argv) > 4 else None
    
    try:
        result = create_xlsx(csv_file, course_id=course_id, canvas_url=canvas_url, token=token)
        print(f"\n✓ Success! Created: {result}")
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == '__main__':
    main()