import cv2
from pyzbar.pyzbar import decode
import urllib.parse
import re
import requests
import tldextract
import time
import idna
import unidecode

# Known URL shortener domains
url_shorteners = {
    'bit.ly', 'tinyurl.com', 't.co', 'goo.gl', 'tiny.cc',
    'ow.ly', 'is.gd', 'buff.ly', 'adf.ly', 'bitly.com',
    'rebrand.ly', 'cutt.ly', 'shorturl.at', 'tiny.one',
    'rotf.lol', 'shorturl.asia'
}

# Common malicious patterns
suspicious_patterns = [
    r'(?i)\.exe$',  # Executable files
    r'(?i)\.bat$',  # Batch files
    r'(?i)\.ps1$',  # PowerShell scripts
    r'(?i)phish',   # Potential phishing
    r'(?i)login',   # Login-related (potential phishing)
    r'(?i)password',# Password-related
    r'(?i)bank',    # Banking-related
    r'(?i)wallet',  # Crypto wallet related
    r'data:text/html', # Data URLs
    r'javascript:', # JavaScript protocol
]

# Known safe domains
safe_domains = {
    'google.com',
    'microsoft.com',
    'apple.com',
    'amazon.com',
    'github.com'
}

# Request headers to mimic a real browser
headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
}

# Define a set of homoglyphs (characters that look similar but are different)
homoglyph_map = {
    'a': ['а'], 'b': ['Ь', 'б'], 'c': ['с'], 'e': ['е'],
    'h': ['н'], 'i': ['і', '1'], 'l': ['і', '1'], 'o': ['о'],
    'p': ['р'], 's': ['ѕ'], 't': ['т'], 'x': ['х'], 'y': ['у'],
    'z': ['ѕ']
}

def is_shortlink(url):
    """Check if URL is from a known URL shortener"""
    try:
        domain = tldextract.extract(url).registered_domain
        return domain in url_shorteners
    except:
        return False

def safely_resolve_url(url, max_redirects=5):
    """Safely resolve shortened URL without actually visiting the final webpage"""
    redirect_chain = []
    current_url = url
    
    try:
        for _ in range(max_redirects):
            redirect_chain.append(current_url)
            head_response = requests.head(current_url, headers=headers, allow_redirects=False, timeout=5)
            
            if head_response.status_code not in [301, 302, 303, 307, 308]:
                break
            
            current_url = head_response.headers.get('location')
            
            if current_url and not current_url.startswith(('http://', 'https://')):
                current_url = urllib.parse.urljoin(url, current_url)
            
            if not current_url:
                break
            
            time.sleep(0.1)
        
        return current_url, redirect_chain, None
    
    except requests.exceptions.RequestException as e:
        return None, redirect_chain, f"Error resolving URL: {str(e)}"

def contains_homoglyphs(url):
    """Check for homoglyphs or visually similar characters."""
    # Normalize the URL to remove accents (optional)
    normalized_url = unidecode.unidecode(url)
    
    # Check for homoglyphs by comparing characters
    for char, homoglyphs in homoglyph_map.items():
        for homoglyph in homoglyphs:
            if homoglyph in url and homoglyph != char:
                return True  # Found a homoglyph pair
    return False

def detect_homograph_attack(domain):
    """Check if domain contains characters that could be used in homograph attacks."""
    try:
        domain_unicode = idna.decode(domain)  # Convert to Unicode (e.g., for Punycode domains)
    except idna.IDNAError:
        domain_unicode = domain  # If IDNA fails, keep the original domain
    
    # Check if the domain contains suspicious homoglyphs
    if contains_homoglyphs(domain_unicode):
        return True
    return False

def analyze_content(content):
    """Analyze decoded content for security risks"""
    risks = []
    risk_level = "LOW"
    
    try:
        parsed = urllib.parse.urlparse(content)
        if parsed.scheme:
            if is_shortlink(content):
                final_url, redirects, error = safely_resolve_url(content)
                
                if error:
                    risks.append(f"Error resolving shortened URL: {error}")
                    risk_level = "HIGH"
                else:
                    redirect_risks = analyze_redirect_chain(redirects)
                    risks.extend(redirect_risks)
                    
                    if redirect_risks:
                        risk_level = "HIGH"
                    
                    content = final_url
                    risks.append(f"URL redirect chain: {' -> '.join(redirects)}")
            
            domain_info = tldextract.extract(content)
            domain = f"{domain_info.domain}.{domain_info.suffix}"
            
            if contains_homoglyphs(content):
                risks.append("URL contains visually similar characters (homoglyphs)")
                risk_level = max(risk_level, "MEDIUM")

            if domain not in safe_domains:
                risks.append(f"Unknown domain: {domain}")
                risk_level = max(risk_level, "MEDIUM")
            
            if detect_homograph_attack(domain):
                risks.append(f"Suspicious homograph attack detected in domain: {domain}")
                risk_level = "HIGH"
            
            for pattern in suspicious_patterns:
                if re.search(pattern, content):
                    risks.append(f"Suspicious pattern found: {pattern}")
                    risk_level = "HIGH"
            
            decoded_url = urllib.parse.unquote(content)
            if decoded_url != content:
                risks.append("URL contains encoded characters")
                risk_level = max(risk_level, "MEDIUM")
            
            if len(domain_info.subdomain.split('.')) > 3:
                risks.append("Suspicious number of subdomains")
                risk_level = "HIGH"
    
    except Exception as e:
        risks.append(f"Error analyzing URL: {str(e)}")
        risk_level = "UNKNOWN"
    
    return {
        "content": content,
        "risk_level": risk_level,
        "risks": risks,
        "is_malicious": risk_level == "HIGH",
        "recommendation": "BLOCK" if risk_level == "HIGH" else "WARN" if risk_level == "MEDIUM" else "ALLOW"
    }

def analyze_redirect_chain(redirect_chain):
    """Analyze the redirect chain for suspicious patterns"""
    risks = []
    
    if len(redirect_chain) > 3:
        risks.append(f"Suspicious number of redirects: {len(redirect_chain)}")
    
    has_http = any(url.startswith('http://') for url in redirect_chain)
    has_https = any(url.startswith('https://') for url in redirect_chain)
    if has_http and has_https:
        risks.append("Mixed HTTP/HTTPS redirects detected")
    
    domains = [tldextract.extract(url).suffix for url in redirect_chain]
    if len(set(domains)) > 2:
        risks.append(f"Multiple country domains in redirect chain: {', '.join(set(domains))}")
    
    return risks


def decode_qr(image_path):
    """Decode QR code from image"""
    try:
        image = cv2.imread(image_path)
        decoded_objects = decode(image)
        if not decoded_objects:
            return None, "No QR code found in image"
        return decoded_objects[0].data.decode('utf-8'), None
    except Exception as e:
        return None, f"Error decoding QR code: {str(e)}"

def check_qr_safety(image_path):
    """Main method to check QR code safety"""
    content, error = decode_qr(image_path)
    if error:
        return {"error": error}
    
    return analyze_content(content)

# Example usage
def main():
    result = check_qr_safety("images/mal5.png")
    
    if "error" in result:
        print(f"Error: {result['error']}")
    else:
        print(f"Content: {result['content']}")
        print(f"Risk Level: {result['risk_level']}")
        print(f"Risks Found: {result['risks']}")
        print(f"Recommendation: {result['recommendation']}")

if __name__ == "__main__":
    main()
