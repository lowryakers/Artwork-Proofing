"""Synthetic live-text label PDFs for the live-text tests, built at runtime.

make_label() draws a label the way an artwork file carries it: an ingredient
frame that wraps across lines, a Nutrition Facts table whose %DV column is a
separate right-aligned text run on each row, and a Contains line. rotate=180
puts the panel upside down, as on the pouch back panels. outline() turns a
live PDF into a print-like twin with no text layer (rendered to an image).
"""
import fitz

INGREDIENTS = ('Grass Fed Beef Protein Isolate, Bovine Collagen Peptides, Coconut Milk Powder, '
               'Natural Flavor, MCT Oil Powder, contains less than 2% of: Cocoa Powder (processed with '
               'alkali), Himalayan Pink Salt, Stevia Leaf Extract (Reb A & Reb M), Xanthan Gum, Guar Gum')
CONTAINS = 'Contains: Milk'
DV_ROWS = (('Total Fat 1.5g', '2%'), ('Saturated Fat 1g', '5%'), ('Trans Fat 0g', ''),
           ('Cholesterol 0mg', '0%'), ('Sodium 330mg', '14%'), ('Total Carbohydrate 4g', '1%'),
           ('Dietary Fiber 0g', '0%'), ('Total Sugars 1g', ''), ('Includes 0g Added Sugars', '0%'),
           ('Protein 26g', ''), ('Vitamin D 0mcg', '0%'), ('Calcium 20mg', '2%'),
           ('Iron 0.2mg', '0%'), ('Potassium 149mg', '4%'))


def make_label(path, ingredients=INGREDIENTS, contains=CONTAINS, dv_rows=DV_ROWS, rotate=0):
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    x0, y, right = 60, 80, 300
    W, H = page.rect.width, page.rect.height
    # Upside down: each run mirrored through the page centre and rotated in place.
    flip = (lambda x, y: (W - x, H - y)) if rotate == 180 else (lambda x, y: (x, y))
    ins = lambda pt, text, size=9: page.insert_text(flip(*pt), text, fontsize=size, rotate=rotate)
    ins((x0, y), 'Nutrition Facts', 16)
    y += 18
    ins((x0, y), '1 serving per container')
    y += 12
    ins((x0, y), 'Serving size 1 bottle (33g)')
    y += 16
    ins((right - 62, y), '% Daily Value*', 8)
    for label, dv in dv_rows:
        y += 12
        ins((x0, y), label)
        if dv:
            ins((right - 6 * len(dv), y), dv)
    y += 16
    ins((x0, y), '*The % Daily Value (DV) tells you how much a nutrient', 7)
    # Ingredient frame: one text block, wrapped by the box width.
    rect = fitz.Rect(x0, y + 20, right, y + 140)
    if rotate == 180:
        rect = fitz.Rect(W - rect.x1, H - rect.y1, W - rect.x0, H - rect.y0)
    page.insert_textbox(rect, 'Ingredients: ' + ingredients, fontsize=8, rotate=rotate)
    if contains:
        ins((x0, y + 160), contains, 8)
    ins((380, 120), 'BEEF PROTEIN', 20)
    doc.save(path)
    doc.close()
    return path


def outline(live_path, out_path):
    """A print-file twin with no text layer: every glyph converted to vector
    paths (as outlining fonts in the design app does), via SVG."""
    src = fitz.open(live_path)
    svg = src[0].get_svg_image(text_as_path=True)
    src.close()
    pdf = fitz.open(stream=svg.encode(), filetype='svg').convert_to_pdf()
    with open(out_path, 'wb') as f:
        f.write(pdf)
    return out_path
