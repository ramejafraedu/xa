import re
import sys

filepath = 'static/index.html'
with open(filepath, 'r', encoding='utf-8') as f:
    html = f.read()

html = re.sub(r'<style id="modern-redesign">.*?</style>\s*', '', html, flags=re.DOTALL)

with open(filepath, 'w', encoding='utf-8') as f:
    f.write(html)
print("Removed modern-redesign block from index.html")