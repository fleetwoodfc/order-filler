#!/usr/bin/env python3
"""
RadLex CSV to JSON Parser

This utility script parses RadLex CSV files and converts them to JSON format
for easier import into the healthcare system.

RadLex is a comprehensive lexicon of radiology terms developed by the
Radiological Society of North America (RSNA). It provides standardized
terminology for radiology procedures, anatomy, and findings.

Usage:
    python scripts/parse_radlex.py input.csv output.json

CSV Format Expected:
    The script expects a CSV file with columns like:
    - RadLex ID (RID)
    - Preferred Name
    - Definition
    - Synonyms
    - Parent concepts
    
Output JSON Format:
    [
        {
            "code": "RID10321",
            "name": "CT chest with contrast",
            "definition": "...",
            "synonyms": ["Chest CT", "..."],
            "category": "Procedure"
        },
        ...
    ]

Note: This is a utility script for optional seed data import.
"""

import csv
import json
import sys
import argparse
from pathlib import Path


def parse_radlex_csv(csv_path):
    """
    Parse RadLex CSV file.
    
    Args:
        csv_path: Path to RadLex CSV file
    
    Returns:
        list of dict with parsed RadLex entries
    """
    entries = []
    
    with open(csv_path, 'r', encoding='utf-8') as csvfile:
        # Try to auto-detect CSV format
        sample = csvfile.read(4096)
        csvfile.seek(0)
        
        sniffer = csv.Sniffer()
        try:
            dialect = sniffer.sniff(sample)
            has_header = sniffer.has_header(sample)
        except csv.Error:
            dialect = csv.excel
            has_header = True
        
        reader = csv.DictReader(csvfile, dialect=dialect) if has_header else csv.reader(csvfile, dialect=dialect)
        
        for row in reader:
            if isinstance(row, dict):
                # DictReader mode
                entry = parse_radlex_row_dict(row)
            else:
                # List mode - create dict with positional fields
                entry = parse_radlex_row_list(row)
            
            if entry:
                entries.append(entry)
    
    return entries


def parse_radlex_row_dict(row):
    """
    Parse a RadLex CSV row in dictionary format.
    
    Handles various possible column names from different RadLex distributions.
    """
    entry = {}
    
    # Map common column name variations
    column_mappings = {
        'code': ['RadLex ID', 'RID', 'Code', 'ID', 'Identifier'],
        'name': ['Preferred Name', 'Name', 'Term', 'Label', 'Preferred Label'],
        'definition': ['Definition', 'Description'],
        'synonyms': ['Synonyms', 'Synonym', 'Alternative Names'],
        'parent': ['Parent', 'Parent Concept', 'Superclass'],
        'category': ['Category', 'Type', 'Concept Type']
    }
    
    # Extract fields using flexible column matching
    for field, possible_columns in column_mappings.items():
        for col in possible_columns:
            if col in row and row[col]:
                if field == 'synonyms':
                    # Split synonyms by common delimiters
                    synonyms = row[col].replace(';', ',').replace('|', ',')
                    entry[field] = [s.strip() for s in synonyms.split(',') if s.strip()]
                else:
                    entry[field] = row[col].strip()
                break
    
    # Ensure we have at least a code or name
    if not entry.get('code') and not entry.get('name'):
        return None
    
    # Default empty lists for optional fields
    if 'synonyms' not in entry:
        entry['synonyms'] = []
    
    return entry


def parse_radlex_row_list(row):
    """
    Parse a RadLex CSV row in list format (no header).
    
    Assumes common column order: Code, Name, Definition, ...
    """
    if len(row) < 2:
        return None
    
    entry = {
        'code': row[0].strip() if row[0] else None,
        'name': row[1].strip() if len(row) > 1 and row[1] else None,
        'definition': row[2].strip() if len(row) > 2 and row[2] else '',
        'synonyms': []
    }
    
    # Additional columns might be synonyms or parent info
    if len(row) > 3 and row[3]:
        synonyms = row[3].replace(';', ',').replace('|', ',')
        entry['synonyms'] = [s.strip() for s in synonyms.split(',') if s.strip()]
    
    return entry if entry['code'] or entry['name'] else None


def filter_radlex_entries(entries, category=None, search_term=None):
    """
    Filter RadLex entries by category or search term.
    
    Args:
        entries: List of RadLex entries
        category: Filter by category (e.g., "Procedure", "Anatomy")
        search_term: Search in name, definition, or synonyms
    
    Returns:
        Filtered list of entries
    """
    filtered = entries
    
    if category:
        filtered = [e for e in filtered if e.get('category', '').lower() == category.lower()]
    
    if search_term:
        search_lower = search_term.lower()
        filtered = [
            e for e in filtered
            if search_lower in e.get('name', '').lower()
            or search_lower in e.get('definition', '').lower()
            or any(search_lower in syn.lower() for syn in e.get('synonyms', []))
        ]
    
    return filtered


def save_json(entries, output_path, pretty=True):
    """
    Save entries to JSON file.
    
    Args:
        entries: List of entries to save
        output_path: Path to output JSON file
        pretty: Whether to pretty-print JSON
    """
    with open(output_path, 'w', encoding='utf-8') as jsonfile:
        if pretty:
            json.dump(entries, jsonfile, indent=2, ensure_ascii=False)
        else:
            json.dump(entries, jsonfile, ensure_ascii=False)
    
    print(f"Saved {len(entries)} entries to {output_path}")


def main():
    """Main entry point for the script."""
    parser = argparse.ArgumentParser(
        description='Parse RadLex CSV file and convert to JSON'
    )
    parser.add_argument(
        'input_csv',
        help='Path to input RadLex CSV file'
    )
    parser.add_argument(
        'output_json',
        help='Path to output JSON file'
    )
    parser.add_argument(
        '--category',
        help='Filter by category (e.g., Procedure, Anatomy)',
        default=None
    )
    parser.add_argument(
        '--search',
        help='Filter by search term in name/definition/synonyms',
        default=None
    )
    parser.add_argument(
        '--compact',
        action='store_true',
        help='Output compact JSON (not pretty-printed)'
    )
    
    args = parser.parse_args()
    
    # Validate input file
    input_path = Path(args.input_csv)
    if not input_path.exists():
        print(f"Error: Input file not found: {input_path}", file=sys.stderr)
        sys.exit(1)
    
    # Parse CSV
    print(f"Parsing RadLex CSV: {input_path}")
    try:
        entries = parse_radlex_csv(input_path)
        print(f"Parsed {len(entries)} entries")
    except Exception as e:
        print(f"Error parsing CSV: {e}", file=sys.stderr)
        sys.exit(1)
    
    # Apply filters if specified
    if args.category or args.search:
        entries = filter_radlex_entries(entries, args.category, args.search)
        print(f"Filtered to {len(entries)} entries")
    
    # Save to JSON
    output_path = Path(args.output_json)
    try:
        save_json(entries, output_path, pretty=not args.compact)
    except Exception as e:
        print(f"Error saving JSON: {e}", file=sys.stderr)
        sys.exit(1)
    
    print("Done!")


if __name__ == '__main__':
    main()
