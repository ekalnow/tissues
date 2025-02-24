import logging
import re
from datetime import datetime
from urllib.parse import urlparse
from typing import Tuple

import requests
from bs4 import BeautifulSoup
from flask import Flask, render_template, request, jsonify
from flask_sqlalchemy import SQLAlchemy

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize Flask app and SQLAlchemy
app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///products.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# Database models
class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    url = db.Column(db.String(500), nullable=False, unique=True)
    website = db.Column(db.String(100), nullable=False)
    current_price = db.Column(db.Float, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class PriceHistory(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=False)
    price = db.Column(db.Float, nullable=False)
    recorded_at = db.Column(db.DateTime, default=datetime.utcnow)

with app.app_context():
    db.create_all()


# ----------------------
# Helper Functions
# ----------------------

def fetch_page(url: str, headers: dict, timeout: int = 10) -> requests.Response:
    """Fetches a page using a persistent session with a timeout."""
    session = requests.Session()
    try:
        response = session.get(url, headers=headers, timeout=timeout)
        response.raise_for_status()
        return response
    except Exception as e:
        logger.error(f"Failed to fetch page: {e}")
        raise Exception(f"Failed to fetch page: {e}")


def convert_arabic_numerals(text: str) -> str:
    """Convert Arabic numerals in a string to English digits."""
    arabic_to_english = str.maketrans('١٢٣٤٥٦٧٨٩٠', '1234567890')
    return text.translate(arabic_to_english)


def extract_numeric_value(text: str) -> float:
    """Extracts numeric value from a text string."""
    numeric_str = ''.join(c for c in text if c.isdigit() or c == '.')
    try:
        return float(numeric_str)
    except Exception as e:
        raise ValueError(f"Could not extract numeric value from '{text}': {e}")


def extract_regine(soup: BeautifulSoup) -> Tuple[str, float]:
    """Extracts product name and price for regine-sa.com."""
    try:
        name = soup.find('h1').get_text(strip=True)
        # Locate an element that contains the currency text
        price_tag = soup.find(lambda tag: tag.name == 'h1' and 'ر.س' in tag.get_text())
        if not price_tag:
            raise ValueError("Price element with 'ر.س' not found.")
        price_text = price_tag.get_text(strip=True)
        # Extract the numeric part (assumes price comes before 'ر.س')
        price = extract_numeric_value(price_text.split('ر.س')[0])
        return name, price
    except Exception as e:
        raise Exception(f"Error extracting data for regine-sa.com: {e}")


def extract_daren(soup: BeautifulSoup, response_text: str) -> Tuple[str, float]:
    """Extracts product name and price for darenfactory.com."""
    try:
        # Try extracting name from <title> or meta tag
        title_tag = soup.find('title')
        name = title_tag.get_text().split('-')[0].strip() if title_tag else None
        if not name:
            meta_title = soup.find('meta', property='og:title')
            if meta_title and meta_title.get('content'):
                name = meta_title['content'].split('-')[0].strip()

        # Use regex to extract Arabic numerals as a candidate price
        arabic_pattern = r'[١٢٣٤٥٦٧٨٩٠]+'
        matches = re.findall(arabic_pattern, response_text)
        price_candidates = [m for m in matches if 2 <= len(m) <= 4]
        if price_candidates:
            price_str = convert_arabic_numerals(price_candidates[0])
            price = float(price_str)
        else:
            raise ValueError("Price not found for darenfactory.com.")

        return name, price
    except Exception as e:
        raise Exception(f"Error extracting data for darenfactory.com: {e}")


def extract_factory(soup: BeautifulSoup, response_text: str) -> Tuple[str, float]:
    """Extracts product name and price for factory-moon.com."""
    try:
        # Extract product name from several candidate locations
        name = None
        for candidate in [
            soup.find('h1', class_='product_title'),
            soup.find('h1'),
            soup.find('meta', {'property': 'og:title'}),
            soup.find('title')
        ]:
            if candidate:
                name = candidate.get('content', candidate.get_text()).strip()
                if name:
                    break
        if not name:
            raise ValueError("Could not find product name.")

        # Price extraction: look for several selectors
        price_element = None
        price_selectors = [
            'span.price', 'p.price', 'div.price', '.product-price',
            '.woocommerce-Price-amount', 'bdi', '.price ins', '.amount'
        ]
        for selector in price_selectors:
            elements = soup.select(selector)
            for element in elements:
                text = element.get_text(strip=True)
                if 'ر.س' in text or 'SAR' in text or any(c in text for c in '١٢٣٤٥٦٧٨٩٠'):
                    price_element = text
                    break
            if price_element:
                break

        if not price_element:
            raise ValueError("Could not find price element.")

        # Clean and convert price text
        price_text = price_element.replace('ر.س', '').replace('SAR', '').strip()
        # Check if price uses Arabic numerals
        if any(c in price_text for c in '١٢٣٤٥٦٧٨٩٠'):
            price_text = convert_arabic_numerals(price_text)
        price = extract_numeric_value(price_text)

        # Sanity check: if price seems unusually high, adjust extraction
        if price > 10000:
            match = re.search(r'\d+\.?\d{0,2}', str(price))
            if match:
                price = float(match.group())

        return name, price
    except Exception as e:
        logger.error(f"Error processing factory-moon.com: {e}")
        raise Exception(f"Error processing factory-moon.com product: {e}")


def extract_generic(soup: BeautifulSoup, response_text: str) -> Tuple[str, float]:
    """Generic extraction for unsupported websites."""
    # Attempt to get product name
    name = None
    for candidate in [
        soup.find('h1'),
        soup.find('meta', {'property': 'og:title'}),
        soup.find('title')
    ]:
        if candidate:
            name = candidate.get('content', candidate.get_text()).strip()
            if name:
                break
    if not name:
        raise Exception("Could not find product name. This website might not be supported yet.")

    # Try multiple patterns for price extraction
    price_patterns = [
        r'\$\s*\d+\.?\d*',
        r'USD\s*\d+\.?\d*',
        r'£\s*\d+\.?\d*',
        r'€\s*\d+\.?\d*',
        r'ر.س\s*\d+\.?\d*',
        r'\d+\.?\d*\s*ر.س',
        r'[١٢٣٤٥٦٧٨٩٠]+',
    ]
    price_text = None
    for pattern in price_patterns:
        matches = re.findall(pattern, response_text)
        if matches:
            price_text = matches[0]
            break

    if not price_text:
        raise Exception("Could not find price. This website might not be supported yet.")

    if any(c in price_text for c in '١٢٣٤٥٦٧٨٩٠'):
        price_text = convert_arabic_numerals(price_text)

    price = extract_numeric_value(price_text)
    return name, price


def extract_product_data(url: str) -> Tuple[str, float, str]:
    """
    Given a product URL, fetches the page and extracts the product name and price.
    Returns a tuple: (name, price, website_domain).
    """
    # Validate URL
    parsed_url = urlparse(url)
    domain = parsed_url.netloc.lower()
    headers = {
        'User-Agent': (
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
            'AppleWebKit/537.36 (KHTML, like Gecko) '
            'Chrome/91.0.4472.124 Safari/537.36'
        )
    }
    response = fetch_page(url, headers)
    soup = BeautifulSoup(response.text, 'html.parser')

    # Dispatch to domain-specific extractor
    if 'regine-sa.com' in domain:
        name, price = extract_regine(soup)
    elif 'darenfactory.com' in domain:
        name, price = extract_daren(soup, response.text)
    elif 'factory-moon.com' in domain:
        name, price = extract_factory(soup, response.text)
    else:
        name, price = extract_generic(soup, response.text)

    return name, price, domain


# ----------------------
# Flask Routes
# ----------------------

@app.route('/')
def index():
    products = Product.query.all()
    return render_template('index.html', products=products)


@app.route('/add_product', methods=['POST'])
def add_product():
    data = request.json
    url = data.get('url', '').strip()

    # Basic URL validation
    if not url or not url.startswith('http'):
        return jsonify({'error': 'Invalid URL provided.'}), 400

    # Check if product already exists
    if Product.query.filter_by(url=url).first():
        return jsonify({'error': 'Product already exists.'}), 400

    try:
        name, price, website = extract_product_data(url)
        if not name or not price:
            raise ValueError("Incomplete product data extracted.")

        # Create new product
        new_product = Product(
            name=name,
            url=url,
            website=website,
            current_price=price
        )
        db.session.add(new_product)
        db.session.commit()

        # Add initial price history
        price_history = PriceHistory(product_id=new_product.id, price=price)
        db.session.add(price_history)
        db.session.commit()

        return jsonify({
            'message': 'Product added successfully.',
            'product': {
                'id': new_product.id,
                'name': new_product.name,
                'price': new_product.current_price
            }
        })
    except Exception as e:
        db.session.rollback()
        logger.exception(f"Error processing product from {url}: {e}")
        return jsonify({'error': f'Error processing {url}: {e}'}), 500


@app.route('/get_products')
def get_products():
    products = Product.query.all()
    result = [{
        'id': p.id,
        'name': p.name,
        'url': p.url,
        'website': p.website,
        'current_price': p.current_price,
        'created_at': p.created_at.isoformat()
    } for p in products]
    return jsonify(result)


if __name__ == '__main__':
    app.run(debug=True)
    