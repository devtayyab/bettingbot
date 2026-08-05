from playwright.sync_api import sync_playwright
import time

def run():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 720}
        )
        page = context.new_page()
        
        url = "https://www.stoiximan.gr/"
        print(f"Opening {url} ...")
        
        try:
            page.goto(url, wait_until="domcontentloaded")
            time.sleep(3)
            
            html = page.content()
            with open("/app/scratch/stoiximan_playwright.html", "w", encoding="utf-8") as f:
                f.write(html)
            print("HTML saved to /app/scratch/stoiximan_playwright.html")
            
            inputs = page.evaluate('''() => {
                const els = document.querySelectorAll('input');
                let res = [];
                els.forEach(e => {
                    res.push({
                        type: e.type,
                        name: e.name,
                        id: e.id,
                        placeholder: e.placeholder,
                        className: e.className,
                        dataQa: e.getAttribute('data-qa')
                    });
                });
                return res;
            }''')
            print("Inputs found:")
            for inp in inputs:
                print(inp)
                
        except Exception as e:
            print(f"Failed: {e}")
        finally:
            browser.close()

if __name__ == "__main__":
    run()
