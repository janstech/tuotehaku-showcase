<?php
if ( ! defined('ABSPATH') ) { exit; }

/**
 * Plugin Name:  Product Search Proxy (WP -> FastAPI)
 * Plugin URI:   https://example.com/
 * Description:  Välittää tuotehakupyynnöt WordPressistä taustajärjestelmään (FastAPI) ja palauttaa JSON-vastauksen käyttöliittymälle.
 * Version:      1.0.0
 * Requires at least: 6.4
 * Requires PHP: 8.0
 * Author:       Jan Sarivuo
 * Author URI:   https://example.com/
 * License:      GPL-2.0-or-later
 * License URI:  https://www.gnu.org/licenses/old-licenses/gpl-2.0.html
 * Text Domain:  product-search-proxy
 * Domain Path:  /languages
 *
 * Tämä on yksinkertaistettu showcase-versio tuotantokäytössä olevasta lisäosasta.
 * Kaikki yrityskohtaiset tiedot, domainit ja salaisuudet on poistettu.
 */

/**
 * Copyright (c) 2025 Jan Sarivuo
 *
 * Tämä koodi on tarkoitettu vain portfolio- ja demonstraatiokäyttöön.
 * Kaupallinen käyttö, uudelleenjakelu tai sisällyttäminen tuotantojärjestelmiin
 * ilman nimenomaista kirjallista lupaa on kielletty.
 */


// WordPress REST API namespace tuotehaulle (wp-json/product-search/v1/…)
define('PRODUCT_SEARCH_PROXY_NS', 'product-search/v1');

/* -------------------------------------------------------
 * Yleinen GET-proxy + kevyt välimuisti
 * ----------------------------------------------------- */

/**
 * Suorittaa HTTP GET -pyynnön taustajärjestelmään ja palauttaa JSON REST -vastauksen.
 *
 * Olettaa seuraavat asetukset WordPressin `options`-taulussa:
 * - product_search_api_base  (string, pakollinen)  Backendin juuri-URL, esim. https://api.example.com
 * - product_search_bearer    (string, valinnainen) Bearer-token Authorization-headeriin
 * - product_search_timeout   (int, valinnainen)    HTTP-aikakatkaisu sekunteina
 * - product_search_cache_sec (int, valinnainen)    Vastausten välimuistin kesto (sekuntia)
 */
function product_search_proxy_get( string $path, array $query = [], int $cacheSecDefault = 60 ) {
    // Luetaan taustajärjestelmän URL asetuksista
    $base = rtrim( get_option('product_search_api_base', ''), '/' );
    if ( empty($base) ) {
        return new \WP_Error(
            'no_base',
            'API:n juuri-URL puuttuu (asetus: product_search_api_base)',
            [ 'status' => 500 ]
        );
    }

    // Valinnainen bearer-token, aikakatkaisu ja välimuistin kesto
    $bearer   = (string) get_option('product_search_bearer', '');
    $timeout  = max(5,  (int) get_option('product_search_timeout',   15));
    $cacheSec = max(0,  (int) get_option('product_search_cache_sec', $cacheSecDefault));

    // Rakennetaan täydellinen URL + kyselyparametrit
    $url = $base . $path;
    if ( ! empty($query) ) {
        $url .= ( strpos($url, '?') === false ? '?' : '&' ) . http_build_query($query);
    }

    // Luodaan välimuistiavain URL:n perusteella
    $cacheKey = 'product_search_proxy_' . md5($url);
    if ( $cacheSec > 0 ) {
        $cached = get_transient($cacheKey);
        if ( $cached !== false ) {
            return rest_ensure_response($cached);
        }
    }

    // HTTP-pyynnön argumentit
    $args = [
        'timeout' => $timeout,
        'headers' => [],
    ];
    if ( $bearer !== '' ) {
        $args['headers']['Authorization'] = 'Bearer ' . $bearer;
    }

    // Suoritetaan GET-pyyntö taustajärjestelmään
    $res = wp_remote_get($url, $args);
    if ( is_wp_error($res) ) {
        return new \WP_Error(
            'upstream_failed',
            $res->get_error_message(),
            [ 'status' => 502 ]
        );
    }

    $code = (int) wp_remote_retrieve_response_code($res);
    $body = (string) wp_remote_retrieve_body($res);

    // Ei-2xx -koodit → välitetään virheenä eteenpäin
    if ( $code < 200 || $code >= 300 ) {
        return new \WP_Error(
            'upstream_status',
            'Taustajärjestelmän virhe: ' . $code,
            [
                'status' => $code ?: 502,
                'body'   => $body,
            ]
        );
    }

    // Dekoodataan JSON-runko
    $json = json_decode($body, true);
    if ( json_last_error() !== JSON_ERROR_NONE ) {
        return new \WP_Error(
            'bad_json',
            'Virheellinen JSON-vastaus taustajärjestelmästä',
            [ 'status' => 502 ]
        );
    }

    // Tallennetaan välimuistiin, jos käytössä
    if ( $cacheSec > 0 ) {
        set_transient($cacheKey, $json, $cacheSec);
    }

    // Palautetaan JSON-data REST-vastauksena
    return new \WP_REST_Response(
        $json,
        200,
        [ 'Content-Type' => 'application/json; charset=UTF-8' ]
    );
}

/* -------------------------------------------------------
 * REST-reitit
 * ----------------------------------------------------- */

add_action('rest_api_init', function () {

    /**
     * Välitetty haku-endpoint: /wp-json/product-search/v1/search
     *
     * Olettaa, että backendissä on yhteensopiva /search -endpoint.
     * Palautettu JSON-rakenne välitetään sellaisenaan, paitsi 404 → tyhjä tuloslista.
     */
    register_rest_route( PRODUCT_SEARCH_PROXY_NS, '/search', [
        'methods'             => 'GET',
        'permission_callback' => '__return_true', // Julkinen haku
        'callback'            => function(\WP_REST_Request $req) {

            // Luetaan hakusana useasta mahdollisesta parametrista
            $q = trim( (string) ( $req->get_param('q')
                ?? $req->get_param('query')
                ?? $req->get_param('hakusana')
                ?? '' ) );

            $qNoSpace = preg_replace('/\s+/', '', $q);

            // Vaaditaan vähintään 3 merkkiä (välilyöntejä ei lasketa)
            if ( $q === '' || mb_strlen($qNoSpace, 'UTF-8') < 3 ) {
                return new \WP_Error(
                    'bad_request',
                    'Hakusanan tulee olla vähintään 3 merkkiä pitkä (välilyöntejä ei lasketa).',
                    [ 'status' => 400 ]
                );
            }

            // Varastosuodatin: stock_only / in_stock_only (oletus: false)
            $stockParam = strtolower( (string) ( $req->get_param('stock_only') ?? $req->get_param('in_stock_only') ?? '' ) );
            $stockOnly  = in_array($stockParam, ['1','true','yes'], true) ? 'true' : 'false';

            // Sivutus turvallisilla min/max-arvoilla
            $limit  = max(1, min(500,  (int)($req->get_param('limit')  ?: 40)));
            $offset = max(0,           (int)($req->get_param('offset') ?: 0  ));

            // Strict-parametri välitetään sellaisenaan (oletus: true)
            $strict = $req->get_param('strict');
            if ($strict === null) {
                $strict = 'true';
            }

            // Rakennetaan kysely taustajärjestelmälle
            $up = [
                'query'         => $q,
                'q'             => $q,
                'hakusana'      => $q,
                'in_stock_only' => $stockOnly,
                'stock_only'    => $stockOnly,
                'strict'        => $strict,
                'limit'         => $limit,
                'offset'        => $offset,
            ];

            // Kutsutaan yleistä proxy-funktiota
            $resp = product_search_proxy_get('/search', $up, 15);

            // Muutetaan 404 → tyhjä lista ja HTTP 200
            if ( is_wp_error($resp) ) {
                $data = $resp->get_error_data();
                if ( ! empty($data['status']) && (int)$data['status'] === 404 ) {
                    return new \WP_REST_Response([
                        'query'     => $q,
                        'suppliers' => [],
                        'has_more'  => false,
                        'limit'     => $limit,
                        'offset'    => $offset,
                    ], 200);
                }
            }

            return $resp;
        },
    ]);

    /**
     * Välitetty kategorioiden haku: /wp-json/product-search/v1/category-products
     *
     * Olettaa, että backendissä on /category-products yhteensopivilla parametreilla.
     */
    register_rest_route( PRODUCT_SEARCH_PROXY_NS, '/category-products', [
        'methods'             => 'GET',
        'permission_callback' => '__return_true',
        'callback'            => function(\WP_REST_Request $req) {

            $category = trim( (string) $req->get_param('category') );
            if ( $category === '' ) {
                return new \WP_Error(
                    'bad_request',
                    'Parametri "category" on pakollinen.',
                    [ 'status' => 400 ]
                );
            }

            $supplier = trim( (string) $req->get_param('supplier') );
            $limit    = max(1, min(200, (int)($req->get_param('limit')  ?: 50 )));
            $offset   = max(0,          (int)($req->get_param('offset') ?: 0  ));

            $q = [
                'category' => $category,
                'limit'    => $limit,
                'offset'   => $offset,
            ];
            if ( $supplier !== '' ) {
                $q['supplier'] = $supplier;
            }

            return product_search_proxy_get('/category-products', $q, 30);
        },
    ]);

});

/* -------------------------------------------------------
 * Shortcode [product_search] – yksinkertainen käyttöliittymä
 * ----------------------------------------------------- */

add_action('init', function () {
    add_shortcode('product_search', function () {
        $uid      = 'product_search_' . wp_generate_password(6, false, false);
        $endpoint = esc_url_raw( rest_url( PRODUCT_SEARCH_PROXY_NS . '/search' ) );

        ob_start(); ?>
        <div id="<?php echo esc_attr($uid); ?>" class="product-search-widget" style="max-width:900px;margin:0 auto;">
            <h1 style="margin:0 0 .75rem 0;">Tuotehaku</h1>

            <div id="<?php echo esc_attr($uid); ?>_wrap" style="display:flex; gap:.5rem; margin:.25rem 0 1rem 0; position:relative;">
                <input id="<?php echo esc_attr($uid); ?>_input" type="search"
                       placeholder="Kirjoita vähintään 3 merkkiä (välilyöntejä ei lasketa)"
                       style="flex:1; padding:.6rem .8rem; border:1px solid #ccc; border-radius:8px;">
            </div>

            <small id="<?php echo esc_attr($uid); ?>_hint" aria-live="polite"
                   style="color:#666;display:block;margin:-.25rem 0 .75rem;"></small>

            <div style="font-size:.9em;color:#666;margin:.25rem 0 1rem;">
                Vinkki: käytä lainausmerkkejä hakeaksesi täsmällistä fraasia (esim. <code>"usb-a to usb-c"</code>).
            </div>

            <label style="display:flex;align-items:center;gap:.45rem;margin-left:.1rem;">
                <input type="checkbox" id="<?php echo esc_attr($uid); ?>_stock" checked>
                Näytä vain varastossa olevat
            </label>

            <div id="<?php echo esc_attr($uid); ?>_status" style="margin:.5rem 0;color:#666;"></div>
            <div id="<?php echo esc_attr($uid); ?>_results" style="display:flex;flex-direction:column;gap:12px;"></div>

            <div style="text-align:center;margin-top:12px;">
                <button id="<?php echo esc_attr($uid); ?>_more"
                        style="display:none;padding:.65rem 1.25rem;border:0;border-radius:10px;background:#198754;color:#fff;font-weight:700;box-shadow:0 2px 6px rgba(0,0,0,.12);cursor:pointer;">
                    Näytä lisää
                </button>
            </div>
        </div>

        <script>
        (function(){
          const uid       = "<?php echo esc_js($uid); ?>";
          const endpoint = "<?php echo esc_url($endpoint); ?>";
          const $wrap     = document.getElementById(uid + "_wrap");
          const $input    = document.getElementById(uid + "_input");
          const $hint     = document.getElementById(uid + "_hint");
          const $stock    = document.getElementById(uid + "_stock");
          const $status   = document.getElementById(uid + "_status");
          const $results = document.getElementById(uid + "_results");
          const $more     = document.getElementById(uid + "_more");

          // --- tyhjennä (×) -nappi hakukenttään ---
          const $host = document.getElementById(uid + "_wrap") || ($input && $input.parentElement);
          if ($host && getComputedStyle($host).position === 'static') $host.style.position = 'relative';

          const $clear = document.createElement('button');
          $clear.type = 'button';
          $clear.setAttribute('aria-label', 'Tyhjennä haku');
          $clear.textContent = '×';
          Object.assign($clear.style, {
            position: 'absolute',
            width: '28px',
            height: '28px',
            border: '0',
            background: 'transparent',
            cursor: 'pointer',
            fontSize: '18px',
            lineHeight: '1',
            zIndex: '999',
            display: 'none',
            color: 'red',
            displayAlign: 'center',
            alignItems: 'center',
            justifyContent: 'center'
          });
          $host.appendChild($clear);

          function placeClear() {
            const hostRect  = $host.getBoundingClientRect();
            const inputRect = $input.getBoundingClientRect();
            const btnW = 28;
            const padRight = 12;
            const left = (inputRect.right - hostRect.left) - btnW - padRight;
            const top  = (inputRect.top - hostRect.top) + (inputRect.height / 2);
            $clear.style.left = left + 'px';
            $clear.style.top  = top  + 'px';
            $clear.style.transform = 'translateY(-50%)';
          }

          function updateClearVisibility() {
            $clear.style.display = $input.value.trim().length > 0 ? 'inline-flex' : 'none';
            placeClear();
          }

          function resetSearch() {
            if (!$input.value) return;
            $input.value = '';
            updateClearVisibility();
            $input.dispatchEvent(new Event('input', { bubbles: true }));
            $input.focus();
          }

          $clear.addEventListener('click', resetSearch);
          $input.addEventListener('keydown', (e) => { if (e.key === 'Escape') { e.preventDefault(); resetSearch(); } });
          ['input','focus','blur'].forEach(ev => $input.addEventListener(ev, () => { updateClearVisibility(); }));
          window.addEventListener('resize', placeClear);
          document.addEventListener('readystatechange', placeClear);
          placeClear();
          updateClearVisibility();

          // ==================== Hakulogiikka ====================

          const PAGE = 40;
          let shown   = 0;
          let offset  = 0;
          let loading = false;
          let lastQ   = "";
          let lastInStock = true;

          let selectedKey = null;
          try { selectedKey = sessionStorage.getItem(`${uid}_selectedProd`) || null; } catch(e){}

          const ok = v => v.replace(/\s+/g,'').length >= 3;

          function esc(s){ return String(s==null?'':s).replace(/[&<>"']/g, m => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m])); }
          function escAttr(s){ return esc(s).replace(/"/g,'&quot;'); }

          function showHint(){ $hint.textContent = ok($input.value) ? '' : 'Kirjoita vähintään 3 merkkiä.'; }

          function setActiveCard(card, key){
            selectedKey = key;
            try { sessionStorage.setItem(`${uid}_selectedProd`, selectedKey); } catch(e){}
            $results.querySelectorAll('.th-card.is-active').forEach(el => el.classList.remove('is-active'));
            card.classList.add('is-active');
          }

          function appendCards(list){
            for (const t of list){
              const p = (typeof t.price_inc === "number") ? t.price_inc :
                        (typeof t.priceInc === "number" ? t.priceInc : null);
              const price = (p != null) ? (p.toFixed(2) + " € (sis. ALV)") : "-";

              const key = String(t.link || t.id || t.ean || t.name || "");
              const div = document.createElement("div");
              div.className = "th-card product-card";
              div.dataset.key = key;
              if (selectedKey && selectedKey === key) div.classList.add("is-active");

              div.innerHTML =
                "<div style='font-weight:600;margin-bottom:6px;'>"+esc(t.name||"Tuote")+"</div>"+
                "<div style='font-size:.85em;color:#666;'>Lähde: "+esc(t.provider||t.supplier||"-")+"</div>"+
                "<div>Hinta: <strong>"+price+"</strong></div>"+
                "<div>Varasto: "+esc(t.stock==null?'-':String(t.stock))+"</div>"+
                "<div>EAN: "+esc(t.ean||"-")+"</div>"+
                "<a class='open-link' href=\""+escAttr(t.link||"#")+"\" target=\"_blank\" rel=\"noopener\">Avaa tuote</a>";

              div.querySelector('.open-link').addEventListener('pointerdown', ()=>{
                setActiveCard(div, key);
              });

              $results.appendChild(div);
            }
            shown += list.length;
          }

          function updateMore(hasMore){
            $more.style.display = hasMore ? "inline-block" : "none";
            if (hasMore) $more.textContent = "Näytä lisää";
          }

          async function fetchPage(first){
            if (loading) return;
            const q = $input.value.trim();
            const inStock = $stock.checked;
            updateClearVisibility();

            if (!ok(q)) {
              $status.textContent = "";
              $results.innerHTML = "";
              $more.style.display = "none";
              shown = 0; offset = 0;
              return;
            }

            if (first || q !== lastQ || inStock !== lastInStock) {
              $results.innerHTML = "";
              shown = 0;
              offset = 0;
              $status.textContent = "Haetaan...";
            }
            loading = true;

            try{
              const params = new URLSearchParams({
                hakusana: q,
                in_stock_only: String(inStock),
                strict: "1",
                limit: String(PAGE),
                offset: String(offset)
              });
              const res = await fetch(endpoint + "?" + params.toString());
              if (!res.ok) {
                $status.innerHTML = "<span style='color:red;'>Virhe "+res.status+"</span>";
                loading = false;
                return;
              }
              const data = await res.json();
              const list = data.suppliers || [];

              if (first && list.length === 0) {
                $status.textContent = "Ei tuloksia";
                updateMore(false);
                loading = false;
                return;
              }

              appendCards(list);
              offset += list.length;
              updateMore(!!data.has_more);

              $status.textContent = data.has_more
                ? ("Näytetään " + shown + " tuotetta (lisää saatavilla)")
                : ("Näytetään " + shown + " tuotetta");

              lastQ = q;
              lastInStock = inStock;
            } catch(e){
              $status.innerHTML = "<span style='color:red;'>Virhe: "+esc(e.message)+"</span>";
            } finally {
              loading = false;
            }
          }

          let timer = 0, composing = false;
          function schedule(){ clearTimeout(timer); timer = setTimeout(() => fetchPage(true), 350); }

          $input.addEventListener('input', () => { showHint(); updateClearVisibility(); if (!composing) schedule(); });
          $input.addEventListener('compositionstart', () => { composing = true; });
          $input.addEventListener('compositionend',   () => { composing = false; schedule(); });
          $input.addEventListener('keydown', e => { if (e.key === 'Enter') fetchPage(true); });
          $stock.addEventListener('change', () => { if (ok($input.value)) fetchPage(true); });
          $more.addEventListener('click', () => fetchPage(false));

          showHint();
          updateClearVisibility();
        })();
        </script>
        <?php
        return ob_get_clean();
    });
});
