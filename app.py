import json
import logging
import re
from datetime import datetime
from urllib.parse import urlparse
from typing import Tuple

import requests
from bs4 import BeautifulSoup
from flask import Flask, render_template, request, jsonify
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize Flask app and SQLAlchemy
app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///products.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)
migrate = Migrate(app, db)

# ----------------------
# Database Models
# ----------------------
class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    url = db.Column(db.String(500), nullable=False)
    website = db.Column(db.String(100), nullable=False)
    current_price = db.Column(db.Float, nullable=False)
    
    # New columns for deeper insights
    brand = db.Column(db.String(200), nullable=True)
    description = db.Column(db.Text, nullable=True)
    image_url = db.Column(db.String(500), nullable=True)
    stock_status = db.Column(db.String(50), nullable=True)
    rating = db.Column(db.Float, nullable=True)
    review_count = db.Column(db.Integer, nullable=True)
    sku = db.Column(db.String(100), nullable=True)
    category = db.Column(db.String(200), nullable=True)
    keywords = db.Column(db.Text, nullable=True)  # Store keywords as comma-separated string
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    __table_args__ = (
        db.UniqueConstraint('url', name='uq_product_url'),
    )

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

def extract_keywords(soup: BeautifulSoup) -> str:
    """
    Extract keywords from various meta tags and content.
    Returns a comma-separated string of unique keywords.
    """
    keywords = set()

    # Try meta keywords
    meta_keywords = soup.find('meta', attrs={'name': 'keywords'})
    if meta_keywords and meta_keywords.get('content'):
        keywords.update([kw.strip().lower() for kw in meta_keywords['content'].split(',')])

    # Try article tags
    article_tags = soup.find('meta', attrs={'property': 'article:tag'})
    if article_tags and article_tags.get('content'):
        keywords.update([tag.strip().lower() for tag in article_tags['content'].split(',')])

    # Try JSON-LD keywords
    json_ld_tags = soup.find_all('script', type='application/ld+json')
    for tag in json_ld_tags:
        try:
            data = json.loads(tag.string)
            if isinstance(data, dict):
                # Look for keywords in various JSON-LD formats
                if 'keywords' in data:
                    if isinstance(data['keywords'], list):
                        keywords.update([k.strip().lower() for k in data['keywords']])
                    elif isinstance(data['keywords'], str):
                        keywords.update([k.strip().lower() for k in data['keywords'].split(',')])
        except (json.JSONDecodeError, AttributeError):
            continue

    # If we have a category, add it as a keyword
    category_meta = soup.find('meta', property='product:category')
    if category_meta and category_meta.get('content'):
        keywords.add(category_meta['content'].strip().lower())

    # Remove empty strings and return as comma-separated string
    keywords = {k for k in keywords if k}
    return ', '.join(sorted(keywords)) if keywords else None

# --- Extraction for Salla ---
def extract_salla(soup: BeautifulSoup, response_text: str) -> dict:
    """
    Extracts product name and price for websites powered by the Salla platform.
    Uses meta tags and JSON-LD data if available.
    Returns a dictionary containing all available product information.
    """
    try:
        # Extract product name
        name = None
        meta_title = soup.find('meta', property='og:title')
        if meta_title and meta_title.get('content'):
            name = meta_title['content'].strip()
        if not name:
            title_tag = soup.find('title')
            if title_tag:
                name = title_tag.get_text().split('-')[0].strip()
        if not name:
            raise ValueError("Could not find product name")

        # Extract price
        price = None
        price_meta = soup.find('meta', property='product:price:amount')
        if price_meta and price_meta.get('content'):
            price = float(price_meta['content'])
        sale_price_meta = soup.find('meta', property='product:sale_price:amount')
        if sale_price_meta and sale_price_meta.get('content'):
            price = float(sale_price_meta['content'])
        if not price:
            raise ValueError("Could not find product price")

        # Extract brand
        brand = None
        brand_meta = soup.find('meta', property='product:brand')
        if brand_meta and brand_meta.get('content'):
            brand = brand_meta['content'].strip()

        # Extract description
        description = None
        desc_meta = soup.find('meta', attrs={'name': 'description'})
        if desc_meta and desc_meta.get('content'):
            description = desc_meta['content'].strip()
        if not description:
            og_desc = soup.find('meta', property='og:description')
            if og_desc and og_desc.get('content'):
                description = og_desc['content'].strip()

        # Extract image URL
        image_url = None
        og_image = soup.find('meta', property='og:image')
        if og_image and og_image.get('content'):
            image_url = og_image['content'].strip()

        # Extract stock status
        stock_status = None
        availability_meta = soup.find('meta', property='product:availability')
        if availability_meta and availability_meta.get('content'):
            stock_status = availability_meta['content'].lower()

        # Extract SKU / Retailer ID
        sku = None
        sku_meta = soup.find('meta', property='product:retailer_item_id')
        if sku_meta and sku_meta.get('content'):
            sku = sku_meta['content'].strip()

        # Extract category
        category = None
        category_meta = soup.find('meta', property='product:category')
        if category_meta and category_meta.get('content'):
            category = category_meta['content'].strip()

        # Extract rating and review count from JSON-LD if available
        rating = None
        review_count = None
        json_ld_tags = soup.find_all('script', type='application/ld+json')
        for tag in json_ld_tags:
            try:
                data = json.loads(tag.string)
                if isinstance(data, dict) and data.get('@type') == 'Product':
                    aggregateRating = data.get('aggregateRating', {})
                    if aggregateRating:
                        rating = float(aggregateRating.get('ratingValue', 0))
                        review_count = int(aggregateRating.get('reviewCount', 0))
                    break
            except (json.JSONDecodeError, ValueError, TypeError, AttributeError):
                continue

        # Extract keywords
        keywords = extract_keywords(soup)

        # Return all extracted data
        return {
            'name': name,
            'price': price,
            'brand': brand,
            'description': description,
            'image_url': image_url,
            'stock_status': stock_status,
            'rating': rating,
            'review_count': review_count,
            'sku': sku,
            'category': category,
            'keywords': keywords
        }

    except Exception as e:
        raise Exception(f"Error extracting data from Salla platform: {e}")

# --- Extraction for Zid ---
def extract_zid(soup: BeautifulSoup, response_text: str) -> dict:
    """
    Extracts product data for websites using the Zid platform.
    Zid pages typically include JSON-LD with structured product data.
    Returns a dictionary containing all available product information.
    """
    try:
        json_ld_tags = soup.find_all('script', type='application/ld+json')
        product_data = None
        for tag in json_ld_tags:
            try:
                data = json.loads(tag.string)
                if isinstance(data, dict) and data.get('@type', '').lower() == 'product':
                    product_data = data
                    break
            except Exception:
                continue
                
        if not product_data:
            raise ValueError("No valid JSON-LD product data found")

        # Extract basic info
        name = product_data.get('name')
        if not name:
            raise ValueError("Product name not found in JSON-LD")
            
        # Extract price from offers
        offers = product_data.get('offers', {})
        if isinstance(offers, list):
            offers = offers[0] if offers else {}
        price = offers.get('price')
        if not price:
            raise ValueError("Product price not found in JSON-LD")
            
        # Extract brand
        brand = product_data.get('brand')
        if isinstance(brand, dict):
            brand = brand.get('name')
            
        # Extract description
        description = product_data.get('description')
        
        # Extract image URL (can be string or list)
        image_url = product_data.get('image')
        if isinstance(image_url, list):
            image_url = image_url[0] if image_url else None
            
        # Extract SKU/Product ID
        sku = product_data.get('sku') or product_data.get('productID')
        
        # Extract stock status
        stock_status = offers.get('availability', '').replace('http://schema.org/', '')
        if stock_status:
            stock_status = stock_status.lower()
            
        # Extract rating and review count
        rating = None
        review_count = None
        aggregate_rating = product_data.get('aggregateRating', {})
        if aggregate_rating:
            try:
                rating = float(aggregate_rating.get('ratingValue', 0))
                review_count = int(aggregate_rating.get('reviewCount', 0))
            except (ValueError, TypeError):
                pass
                
        # Extract category
        category = None
        if 'category' in product_data:
            category = product_data['category']
            if isinstance(category, dict):
                category = category.get('name')
        
        # Extract keywords
        keywords = extract_keywords(soup)

        # Return all extracted data
        return {
            'name': name,
            'price': float(price),
            'brand': brand,
            'description': description,
            'image_url': image_url,
            'stock_status': stock_status,
            'rating': rating,
            'review_count': review_count,
            'sku': sku,
            'category': category,
            'keywords': keywords
        }

    except Exception as e:
        raise Exception(f"Error extracting data from Zid platform: {e}")

# --- Generic Extraction ---
def extract_generic(soup: BeautifulSoup, response_text: str) -> dict:
    """
    Generic extraction for unsupported websites.
    Attempts to extract product information from common meta tags, Open Graph tags,
    and JSON-LD data if available.
    """
    try:
        # Initialize data dictionary
        data = {
            'name': None,
            'price': None,
            'brand': None,
            'description': None,
            'image_url': None,
            'stock_status': None,
            'rating': None,
            'review_count': None,
            'sku': None,
            'category': None,
            'keywords': None
        }
        
        # Try JSON-LD first (most structured)
        json_ld_tags = soup.find_all('script', type='application/ld+json')
        for tag in json_ld_tags:
            try:
                json_data = json.loads(tag.string)
                if isinstance(json_data, dict):
                    if json_data.get('@type', '').lower() == 'product':
                        # Extract from JSON-LD Product
                        data['name'] = json_data.get('name')
                        offers = json_data.get('offers', {})
                        if isinstance(offers, list):
                            offers = offers[0] if offers else {}
                        data['price'] = float(offers.get('price', 0)) or None
                        
                        brand = json_data.get('brand')
                        if isinstance(brand, dict):
                            brand = brand.get('name')
                        data['brand'] = brand
                        
                        data['description'] = json_data.get('description')
                        image = json_data.get('image')
                        if isinstance(image, list):
                            image = image[0] if image else None
                        data['image_url'] = image
                        
                        data['sku'] = json_data.get('sku') or json_data.get('productID')
                        data['stock_status'] = offers.get('availability', '').lower() if offers else None
                        
                        agg_rating = json_data.get('aggregateRating', {})
                        if agg_rating:
                            try:
                                data['rating'] = float(agg_rating.get('ratingValue', 0)) or None
                                data['review_count'] = int(agg_rating.get('reviewCount', 0)) or None
                            except (ValueError, TypeError):
                                pass
                        break
            except (json.JSONDecodeError, ValueError, AttributeError):
                continue
        
        # If name not found in JSON-LD, try meta tags
        if not data['name']:
            # Try Open Graph title
            og_title = soup.find('meta', property='og:title')
            if og_title:
                data['name'] = og_title.get('content')
            
            # Fallback to meta title
            if not data['name']:
                title_tag = soup.find('title')
                if title_tag:
                    # Remove site name if present (usually after "-" or "|")
                    title_text = title_tag.get_text()
                    data['name'] = title_text.split('-')[0].split('|')[0].strip()
        
        # If price not found in JSON-LD, try meta tags and common price patterns
        if not data['price']:
            # Try Open Graph price
            og_price = soup.find('meta', property=['og:price:amount', 'product:price:amount'])
            if og_price:
                try:
                    data['price'] = float(og_price.get('content', 0))
                except (ValueError, TypeError):
                    pass
            
            # Try finding price in specific elements (common patterns)
            if not data['price']:
                price_elements = soup.find_all(['span', 'div', 'p'], 
                    class_=lambda x: x and any(price_class in x.lower() 
                        for price_class in ['price', 'prc', 'amount']))
                for elem in price_elements:
                    price_text = elem.get_text().strip()
                    try:
                        # Extract numeric value from text
                        numeric_value = extract_numeric_value(price_text)
                        if numeric_value:
                            data['price'] = float(numeric_value)
                            break
                    except (ValueError, TypeError):
                        continue
        
        # Get description if not found in JSON-LD
        if not data['description']:
            meta_desc = soup.find('meta', attrs={'name': 'description'})
            if meta_desc:
                data['description'] = meta_desc.get('content')
            if not data['description']:
                og_desc = soup.find('meta', property='og:description')
                if og_desc:
                    data['description'] = og_desc.get('content')
        
        # Get image if not found in JSON-LD
        if not data['image_url']:
            og_image = soup.find('meta', property='og:image')
            if og_image:
                data['image_url'] = og_image.get('content')
        
        # Try to extract brand from title or meta tags if not found
        if not data['brand']:
            meta_brand = soup.find('meta', property=['og:brand', 'product:brand'])
            if meta_brand:
                data['brand'] = meta_brand.get('content')
        
        # Extract keywords
        data['keywords'] = extract_keywords(soup)

        # Validate required fields
        if not data['name']:
            raise ValueError("Could not find product name")
        if not data['price']:
            raise ValueError("Could not find product price")
            
        return data

    except Exception as e:
        raise Exception(f"Error in generic extraction: {e}")

# --- Main Extraction Dispatcher ---
def extract_product_data(url: str) -> dict:
    """
    Given a product URL, fetches the page and extracts the product data.
    The function first determines whether the page is from Salla or Zid and then
    calls the respective extraction function. If neither applies, it uses a generic extractor.
    Returns a dictionary containing all available product information.
    """
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) ' +
                     'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }
    try:
        response = fetch_page(url, headers)
        soup = BeautifulSoup(response.text, 'html.parser')
        domain = urlparse(url).netloc.lower()

        # Detect Salla using specific meta indicators
        is_salla = any([
            soup.find('meta', property='product:retailer_item_id'),
            soup.find('meta', property='product:price:currency', content='SAR'),
            soup.find('link', {'rel': 'canonical', 'href': lambda x: x and 'salla.sa' in x})
        ])

        # If not Salla, try to detect Zid via JSON-LD structured product data
        is_zid = False
        if not is_salla:
            json_ld_tags = soup.find_all('script', type='application/ld+json')
            for tag in json_ld_tags:
                try:
                    data = json.loads(tag.string)
                    if isinstance(data, dict) and data.get('@type', '').lower() == 'product':
                        is_zid = True
                        break
                except Exception:
                    continue

        # Extract data based on platform
        if is_salla:
            data = extract_salla(soup, response.text)
        elif is_zid:
            data = extract_zid(soup, response.text)
        else:
            data = extract_generic(soup, response.text)

        data['website'] = domain
        return data

    except Exception as e:
        logger.error(f"Error processing URL {url}: {e}")
        raise Exception(f"Failed to extract product data: {e}")

# ----------------------
# Flask Routes
# ----------------------
@app.route('/')
def index():
    products = Product.query.all()
    return render_template('index.html', products=products)

@app.route('/add_products', methods=['POST'])
def add_products():
    data = request.json
    urls = data.get('urls', [])
    if not urls or not isinstance(urls, list):
        return jsonify({'error': 'Invalid URLs provided. Please provide a list of URLs.'}), 400

    # Validate max number of URLs
    MAX_URLS = 10  # Prevent too many URLs at once
    if len(urls) > MAX_URLS:
        return jsonify({'error': f'Too many URLs. Maximum {MAX_URLS} URLs allowed per request.'}), 400

    results = []
    successful_products = []  # Track successful products for batch commit

    for url in urls:
        url = url.strip()
        
        # Enhanced URL validation
        try:
            parsed_url = urlparse(url)
            if not all([parsed_url.scheme in ['http', 'https'], parsed_url.netloc]):
                results.append({'url': url, 'error': 'Invalid URL format. Must be a valid HTTP/HTTPS URL.'})
                continue
        except Exception:
            results.append({'url': url, 'error': 'Invalid URL format.'})
            continue

        # Check if product already exists
        if Product.query.filter_by(url=url).first():
            results.append({'url': url, 'error': 'Product already exists.'})
            continue

        try:
            product_data = extract_product_data(url)
            
            # Validate required fields
            required_fields = ['name', 'price', 'website']
            missing_fields = [field for field in required_fields if not product_data.get(field)]
            if missing_fields:
                results.append({'url': url, 'error': f'Missing required fields: {", ".join(missing_fields)}'})
                continue

            new_product = Product(
                name=product_data['name'],
                url=url,
                website=product_data['website'],
                current_price=product_data['price'],
                brand=product_data.get('brand'),
                description=product_data.get('description'),
                image_url=product_data.get('image_url'),
                stock_status=product_data.get('stock_status'),
                rating=product_data.get('rating'),
                review_count=product_data.get('review_count'),
                sku=product_data.get('sku'),
                category=product_data.get('category'),
                keywords=product_data.get('keywords')
            )
            
            # Add to batch instead of immediate commit
            successful_products.append((new_product, product_data['price']))
            db.session.add(new_product)

        except Exception as e:
            logger.error(f"Error processing URL {url}: {str(e)}", exc_info=True)
            results.append({'url': url, 'error': f'Failed to process URL: {str(e)}'})

    try:
        # First commit all products to get their IDs
        db.session.flush()
        
        # Now create price histories with valid product IDs
        for product, price in successful_products:
            price_history = PriceHistory(
                product_id=product.id,  # Now product.id is available
                price=price
            )
            db.session.add(price_history)
            
            results.append({
                'url': product.url,
                'message': 'Product added successfully',
                'product': {
                    'id': product.id,
                    'name': product.name,
                    'url': product.url,
                    'website': product.website,
                    'current_price': product.current_price,
                    'brand': product.brand,
                    'description': product.description,
                    'image_url': product.image_url,
                    'stock_status': product.stock_status,
                    'rating': product.rating,
                    'review_count': product.review_count,
                    'sku': product.sku,
                    'category': product.category,
                    'keywords': product.keywords,
                    'created_at': product.created_at.isoformat()
                }
            })
            
        # Finally commit everything
        db.session.commit()
            
    except Exception as e:
        db.session.rollback()
        logger.error("Failed to commit batch of products", exc_info=True)
        return jsonify({
            'error': 'Database error occurred while saving products',
            'details': str(e)
        }), 500

    return jsonify(results), 201

@app.route('/get_products')
def get_products():
    products = Product.query.all()
    result = [{
        'id': p.id,
        'name': p.name,
        'url': p.url,
        'website': p.website,
        'current_price': p.current_price,
        'brand': p.brand,
        'description': p.description,
        'image_url': p.image_url,
        'stock_status': p.stock_status,
        'rating': p.rating,
        'review_count': p.review_count,
        'sku': p.sku,
        'category': p.category,
        'keywords': p.keywords,
        'created_at': p.created_at.isoformat()
    } for p in products]
    return jsonify(result)

@app.route('/delete_all_products', methods=['POST'])
def delete_all_products():
    try:
        # Delete all price history records first (due to foreign key constraint)
        PriceHistory.query.delete()
        # Delete all products
        Product.query.delete()
        # Commit the changes
        db.session.commit()
        return jsonify({'message': 'All products and their price histories have been deleted successfully'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True)
