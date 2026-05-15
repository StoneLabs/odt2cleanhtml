import re
import argparse
import sys
import zipfile, subprocess, re, sys, os
from bs4 import BeautifulSoup, NavigableString, Tag
from pathlib import Path
import rich
print = rich.print

# Mapping from <font size="N"> to points
FONT_SIZE_MAP = {
    '1': 8,
    '2': 10,
    '3': 12,
    '4': 14,
    '5': 18,
    '6': 24,
    '7': 36
}

# Allowed inline tags to keep in the output
ALLOWED_TAGS = {'i', 'b', 'strong', 'u', 'sup'}
BLOCK_TAGS = {'h1','h2','h3','h4','h5','h6','p','div','blockquote','li'}

def extract_css_rules(soup):
    """Parse the <style> block and return a list of (tag, class, font_size_pt) rules."""
    rules = []
    style_tag = soup.find('style')
    if not style_tag or not style_tag.string:
        return rules

    # Find all rule blocks: selector { ... }
    block_pattern = re.compile(r'([^{]+)\s*\{([^}]+)\}', re.DOTALL)
    for match in block_pattern.finditer(style_tag.string):
        selector_str = match.group(1).strip()
        properties = match.group(2)
        # Look for font-size: XXpt
        font_match = re.search(r'font-size\s*:\s*(\d+)pt', properties)
        if not font_match:
            continue
        size = int(font_match.group(1))

        # Simple selector parsing: tag.class or tag:pseudo-class
        # We'll extract tag name and an optional class.
        # Selector may be something like "h1.cjk", "p.western", "a:link"
        parts = selector_str.split('.')
        tag = parts[0].split(':')[0].strip().lower()
        cls = parts[1].split(':')[0].strip() if len(parts) > 1 else None
        rules.append((tag, cls, size))
    return rules

def compute_font_size(elem, parent_size, css_rules):
    """Return the effective font size in points for this element."""
    size = parent_size

    # Apply CSS rules from the <style> block (last matching rule wins)
    if isinstance(elem, Tag):
        for tag, cls, css_size in css_rules:
            if elem.name.lower() == tag:
                if cls is None or cls in (elem.get('class') or []):
                    size = css_size

        # Inline style attribute (font-size: XXpt)
        style_attr = elem.get('style')
        if style_attr:
            m = re.search(r'font-size\s*:\s*(\d+)pt', style_attr)
            if m:
                size = int(m.group(1))

        # <font size="N"> tag
        if elem.name.lower() == 'font' and elem.get('size'):
            size_key = elem['size'].strip()
            if size_key in FONT_SIZE_MAP:
                size = FONT_SIZE_MAP[size_key]

    return size

def process_node(node, current_size, css_rules):
    """Recursively process a BeautifulSoup node and return cleaned string."""
    if isinstance(node, NavigableString):
        text = re.sub(r'\n+', ' ', node.string)
        if current_size != 12:
            em = current_size / 12.0
            return f'<span style="font-size:{em}em">{text}</span>'
        return text

    if not isinstance(node, Tag):
        return ''

    tag = node.name.lower()
    if tag in ALLOWED_TAGS:
        # Compute the font size that will be inherited by this element's children
        new_size = compute_font_size(node, current_size, css_rules)
        # Process children and wrap only the tag (no extra span)
        content = ''.join(process_node(child, new_size, css_rules) for child in node.children)
        return f'<{tag}>{content}</{tag}>'
    else:
        # Non‑allowed tag: just pass the font size down, do not output any tag
        new_size = compute_font_size(node, current_size, css_rules)
        return ''.join(process_node(child, new_size, css_rules) for child in node.children)

def clean_html(html):
    """Main function: clean the HTML and return the cleaned text."""
    soup = BeautifulSoup(html, 'html.parser')
    body = soup.body
    if not body:
        return ''

    css_rules = extract_css_rules(soup)

    # Process each block‑level child (h1, p, etc.) in order
    blocks = []
    for child in body.children:
        if isinstance(child, Tag) and child.name.lower() in BLOCK_TAGS:
            block_str = process_node(child, current_size=12, css_rules=css_rules)
            block_str = block_str.strip()
            if block_str:
                blocks.append(block_str)

    return '\n\n'.join(blocks)

def process(input_path):
    if not Path(input_path).exists():
        print(f"[bold red]Error: file not found: {input_path}")
        return

    if Path(input_path).suffix.lower() != '.odt':
        print(f"[bold red]Error: expected .odt file, got: {Path(input_path).suffix}")
        return

    patched = '/tmp/input_patched.odt'
    html_dir = '/tmp/input_patched/'
    html_file = '/tmp/input_patched/input_patched.html'
    html_cleaned = html_file + ".cleaned.html"

    print(f"=> Reading {input_path}...")
    with zipfile.ZipFile(input_path) as z:
        content = z.read('content.xml').decode()

    empty_paras = len(re.findall(r'<text:p[^>]*/>', content))
    print(f" -> Found {empty_paras} empty [reset]paragraph(s), patching...")
    content = re.sub(
        r'<text:p[^>]*/>', 
        '<text:p text:style-name="Text_20_Body">EMPTY_PARA_PLACEHOLDER</text:p>',
        content
    )

    print(f"=> Writing patched ODT to {patched}...")
    with zipfile.ZipFile(patched, 'w') as zout:
        with zipfile.ZipFile(input_path) as zin:
            for item in zin.infolist():
                if item.filename == 'content.xml':
                    zout.writestr(item, content)
                else:
                    zout.writestr(item, zin.read(item.filename))

    print(f"=> Running libreoffice convert on {patched}...", end="")
    result = subprocess.run(['libreoffice', "--headless", "--convert-to", "html", "--outdir", html_dir, patched])


    if result.returncode != 0:
        print(" FAILED.")
        print(f"[bold red]Error: pandoc failed:\n{result.stderr}")
        return
    else: 
        print(f" OK.")

    print(f" -> Checking if output html {html_file} exists...")
    if not os.path.isfile(html_file):
        print("[bold red]Error. File does not exist!")
        return

    print(f"=> Running html cleanup...")
    with open(html_file, "r", encoding="utf-8") as f:
        raw_html = f.read()

    cleaned = clean_html(raw_html)
    print(f" -> Cleaned html from {len(raw_html)} ch -> {len(cleaned)} ch")

    with open(html_cleaned, 'w') as f:
        f.write(cleaned)
    print(f" -> Saved cleaned html to {html_cleaned} for debugging")

    print("=> Running post processing steps...")
    print(" -> Removing patched markers")
    cleaned = cleaned.replace('EMPTY_PARA_PLACEHOLDER\n', '')
    cleaned = cleaned.replace('EMPTY_PARA_PLACEHOLDER', '') # if last line

    print(" -> Checking for eof marker...", end="")
    if 'EOFEOFEOF' in cleaned:
        print(" Found.")
        cleaned = cleaned[:cleaned.index('EOFEOFEOF')]
        print(" -> Truncated output at eof marker")
    else:
        print(" [reset]None.")

    print(" -> Removing whitespace tags")
    for i in range(1, 10):
        cleaned = re.sub(
            r'<([^<>\s]*)([^<>]*)>(\s*)</\1>', 
            r'\3',
            cleaned
        )

    print(" -> Joining tags")
    for i in range(1, 10):
        cleaned = re.sub(
            r'<([^<>\s]*)([^<>]*)>(.+)</\1><\1\2>', 
            r'<\1\2>\3',
            cleaned
        )
    
    print(" -> Fixing newline quotation mark bug fix")
    cleaned = re.sub(
        r'\n“(<[^<>]*>)', 
        r'\1“',
        cleaned
    )
    

    print("=> Outputting")

    output_dir = Path('md-export')
    print(f" -> Creating output folder {output_dir.resolve()}")
    output_dir.mkdir(exist_ok=True)
    output_path = output_dir / Path(input_path).with_suffix('.html').name

    print(f" -> Writing output to {output_path}...")
    with open(output_path, 'w') as f:
        f.write(cleaned)

    print(f"=> Done. {len(cleaned.splitlines())} lines written to {output_path}")

def main():
    if len(sys.argv) < 2:
        print("Usage: python convert.py <input1.odt> ...")
        sys.exit(1)

    for file in sys.argv[1:]:
        print(f"[bold]Processing file {file}")
        process(file)



if __name__ == "__main__":
    main()