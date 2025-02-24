from flask import Flask, render_template, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
import requests
from bs4 import BeautifulSoup
import os
import re

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///products.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    url = db.Column(db.String(500), nullable=False)
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

@app.route('/')
def index():
    products = Product.query.all()
    return render_template('index.html', products=products)

@app.route('/add_product', methods=['POST'])
def add_product():
    data = request.json
    url = data.get('url')
    
    # Basic URL validation
    if not url or not url.startswith('http'):
        return jsonify({'error': 'Invalid URL'}), 400

    # Check if product already exists
    existing_product = Product.query.filter_by(url=url).first()
    if existing_product:
        return jsonify({'error': 'Product already exists'}), 400

    try:
        # Common headers for all requests
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        
        # Extract domain from URL
        from urllib.parse import urlparse
        domain = urlparse(url).netloc.lower()
        
        # Initialize variables
        name = None
        price = None
        website = domain
        
        # Get the page content
        response = requests.get(url, headers=headers)
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # For regine-sa.com specific scraping
        if 'regine-sa.com' in domain:
            name = soup.find('h1').text.strip()
            price_text = soup.select_one('h1:contains("ر.س")').text.strip()
            price = float(price_text.split('ر.س')[0].strip())
            
        # For darenfactory.com specific scraping
        elif 'darenfactory.com' in domain:
            # Extract name from title
            title_tag = soup.find('title')
            if title_tag:
                name = title_tag.text.split('-')[0].strip()
            else:
                meta_title = soup.find('meta', property='og:title')
                if meta_title:
                    name = meta_title['content'].split('-')[0].strip()

            # Look for Arabic numerals in the HTML
            arabic_pattern = r'[١٢٣٤٥٦٧٨٩٠]+'
            matches = re.findall(arabic_pattern, response.text)
            price_candidates = [m for m in matches if 2 <= len(m) <= 4]
            
            if price_candidates:
                arabic_to_english = str.maketrans('١٢٣٤٥٦٧٨٩٠', '1234567890')
                price = float(price_candidates[0].translate(arabic_to_english))

        # For factory-moon.com specific scraping
        elif 'factory-moon.com' in domain:
            try:
                # Find product name - usually in h1 or product title
                name_element = soup.find('h1', class_='product_title') or soup.find('h1')
                if name_element:
                    name = name_element.text.strip()
                else:
                    # Try meta tags
                    meta_title = soup.find('meta', {'property': 'og:title'})
                    if meta_title and meta_title.get('content'):
                        name = meta_title['content'].strip()
                    else:
                        # Fallback to page title
                        title = soup.find('title')
                        if title:
                            name = title.text.strip()
                
                if not name:
                    raise ValueError("Could not find product name")

                # Find price - look for elements containing ر.س or Arabic numerals
                price_element = None
                price_selectors = [
                    'span.price',
                    'p.price',
                    'div.price',
                    '.product-price',
                    '.woocommerce-Price-amount',
                    'bdi',  # Common WooCommerce price element
                    '.price ins',  # Sale price in WooCommerce
                    '.amount'  # Generic price amount class
                ]
                
                # Debug price finding
                print("Searching for price in HTML:")
                for selector in price_selectors:
                    elements = soup.select(selector)
                    print(f"Selector '{selector}' found {len(elements)} elements:")
                    for element in elements:
                        print(f"- Text: {element.text.strip()}")
                        if 'ر.س' in element.text or 'SAR' in element.text or any(c in element.text for c in '١٢٣٤٥٦٧٨٩٠'):
                            price_element = element
                            break
                    if price_element:
                        break
                
                if price_element:
                    price_text = price_element.text.strip()
                    print(f"Found price text: {price_text}")
                    
                    # Remove currency symbols and text
                    price_text = price_text.replace('ر.س', '').replace('SAR', '').strip()
                    
                    # First try to extract Arabic numerals
                    arabic_nums = ''.join(c for c in price_text if c in '١٢٣٤٥٦٧٨٩٠')
                    if arabic_nums:
                        arabic_to_english = str.maketrans('١٢٣٤٥٦٧٨٩٠', '1234567890')
                        price = float(arabic_nums.translate(arabic_to_english))
                    else:
                        # Try extracting regular numerals
                        numeric_chars = ''.join(c for c in price_text if c.isdigit() or c == '.')
                        # Handle potential multiple decimal points
                        if numeric_chars.count('.') > 1:
                            numeric_chars = numeric_chars.replace('.', '', numeric_chars.count('.') - 1)
                        price = float(numeric_chars)
                    
                    # Sanity check - if price seems too high, it might include extra digits
                    if price > 10000:  # Arbitrary threshold
                        # Try to extract just the main price component
                        main_price = re.search(r'\d+\.?\d{0,2}', str(price))
                        if main_price:
                            price = float(main_price.group())
                else:
                    raise ValueError("Could not find price element")
                    
            except Exception as e:
                print(f"Debug - Error processing factory-moon.com: {str(e)}")
                print("Available price elements:")
                for elem in soup.find_all(['span', 'div', 'p', 'bdi']):
                    if any(term in elem.text for term in ['ر.س', 'SAR', '٫']):
                        print(f"- {elem.text.strip()}")
                raise ValueError(f"Error processing factory-moon.com product: {str(e)}")
            
        # Generic website handling
        else:
            # Try to get product name from common locations
            name = None
            name_candidates = [
                soup.find('h1'),  # Most common for product names
                soup.find('meta', {'property': 'og:title'}),  # Open Graph title
                soup.find('title'),  # Page title
            ]
            
            for candidate in name_candidates:
                if candidate:
                    if hasattr(candidate, 'content') and candidate.get('content'):  # Meta tags
                        name = candidate['content']
                    else:
                        name = candidate.text
                    name = name.strip()
                    break
            
            if not name:
                return jsonify({'error': 'Could not find product name. This website might not be supported yet.'}), 400
            
            # Try to find price using common patterns
            price_patterns = [
                r'\$\s*\d+\.?\d*',  # $XX.XX
                r'USD\s*\d+\.?\d*',  # USD XX.XX
                r'£\s*\d+\.?\d*',  # £XX.XX
                r'€\s*\d+\.?\d*',  # €XX.XX
                r'ر.س\s*\d+\.?\d*',  # ر.س XX.XX (Saudi Riyal)
                r'\d+\.?\d*\s*ر.س',  # XX.XX ر.س
                r'[١٢٣٤٥٦٧٨٩٠]+',  # Arabic numerals
            ]
            
            price_text = None
            for pattern in price_patterns:
                matches = re.findall(pattern, response.text)
                if matches:
                    price_text = matches[0]
                    break
            
            if price_text:
                # Handle Arabic numerals
                if any(c in price_text for c in '١٢٣٤٥٦٧٨٩٠'):
                    arabic_to_english = str.maketrans('١٢٣٤٥٦٧٨٩٠', '1234567890')
                    price_text = price_text.translate(arabic_to_english)
                
                # Extract numeric value
                price = float(''.join(c for c in price_text if c.isdigit() or c == '.'))
            else:
                return jsonify({'error': 'Could not find price. This website might not be supported yet.'}), 400
        
        if not name or not price:
            return jsonify({'error': 'Could not extract all required information. This website might need specific support.'}), 400
        
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
        price_history = PriceHistory(
            product_id=new_product.id,
            price=price
        )
        db.session.add(price_history)
        db.session.commit()

        return jsonify({
            'message': 'Product added successfully',
            'product': {
                'id': new_product.id,
                'name': new_product.name,
                'price': new_product.current_price
            }
        })
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': f'Error processing {domain}: {str(e)}'}), 500

@app.route('/get_products')
def get_products():
    products = Product.query.all()
    return jsonify([{
        'id': p.id,
        'name': p.name,
        'url': p.url,
        'website': p.website,
        'current_price': p.current_price,
        'created_at': p.created_at.isoformat()
    } for p in products])

if __name__ == '__main__':
    app.run(debug=True)
