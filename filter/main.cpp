// ═══════════════════════════════════════════════════════════════
// Marketplace Sniper v5 — C++17 Filter + Direct Telegram
// O(1) baseline lookups · weighted scoring · self-healing notify
// ═══════════════════════════════════════════════════════════════

#define CPPHTTPLIB_OPENSSL_SUPPORT
#include "httplib.h"
#include "nlohmann/json.hpp"

#include <algorithm>
#include <cctype>
#include <chrono>
#include <cmath>
#include <cstdlib>
#include <fstream>
#include <iostream>
#include <mutex>
#include <regex>
#include <sstream>
#include <string>
#include <string_view>
#include <thread>
#include <unordered_map>
#include <vector>

using json = nlohmann::json;
using Clock = std::chrono::high_resolution_clock;

// ─── Configuration ──────────────────────────────────────────

static constexpr int PORT = 8002;
static constexpr int TELEGRAM_RETRY_MAX = 3;
static constexpr int TELEGRAM_RETRY_DELAY_MS = 2000;

// ─── Structures ─────────────────────────────────────────────

struct Modifier {
  std::regex pattern;
  double weight;
  std::string label;
};

struct MileageBracket {
  int max_km;
  double penalty;
};

struct ModelBaseline {
  std::string name;
  std::vector<std::regex> patterns;
  double baseline;
  std::vector<Modifier> modifiers;
  std::vector<MileageBracket> mileage_brackets;
};

struct Baselines {
  std::vector<ModelBaseline> models;
  // O(1) keyword → model index map
  std::unordered_map<std::string, size_t> keyword_index;
};

struct MatchResult {
  bool matched = false;
  std::string model_name;
  double adjusted_baseline = 0.0;
  std::vector<std::string> applied_modifiers;
  double mileage_penalty = 0.0;
};

struct Deal {
  std::string title;
  double price;
  double market_value;
  double savings_pct;
  int model_year;
  int mileage_km;
  std::string url;
  std::string phone;
  std::string source;
  std::vector<std::string> modifiers;
};

// ─── Helpers ────────────────────────────────────────────────

static std::string normalize(std::string_view input) {
  std::string out;
  out.reserve(input.size());
  for (const char c : input) {
    if (std::isalnum(static_cast<unsigned char>(c)) || c == ' ') {
      out += static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
    } else {
      out += ' ';
    }
  }
  return out;
}

static std::string url_encode(std::string_view sv) {
  std::ostringstream oss;
  oss << std::uppercase << std::hex;
  for (const char c : sv) {
    if (std::isalnum(static_cast<unsigned char>(c)) || c == '-' || c == '_' ||
        c == '.' || c == '~') {
      oss << c;
    } else {
      const auto uc = static_cast<unsigned int>(static_cast<unsigned char>(c));
      oss << '%';
      if (uc < 16)
        oss << '0'; // zero-pad single hex digits
      oss << uc;
    }
  }
  return oss.str();
}

// ─── Baselines Loader (hot-reload every request) ────────────

static Baselines load_baselines() {
  Baselines b;
  std::ifstream f("/app/data/baselines.json");
  if (!f.is_open()) {
    std::cerr << "[WARN] baselines.json not found, using empty\n";
    return b;
  }

  json j;
  try {
    j = json::parse(f);
  } catch (const json::exception &e) {
    std::cerr << "[ERR] baselines parse: " << e.what() << "\n";
    return b;
  }

  if (!j.contains("models"))
    return b;

  for (size_t idx = 0; idx < j["models"].size(); ++idx) {
    const auto &m = j["models"][idx];
    ModelBaseline mb;
    mb.name = m.value("name", "Unknown");
    mb.baseline = m.value("baseline", 0.0);

    // Compile regex patterns
    if (m.contains("patterns")) {
      for (const auto &p : m["patterns"]) {
        mb.patterns.emplace_back(p.get<std::string>(),
                                 std::regex_constants::icase |
                                     std::regex_constants::optimize);
      }
    }

    // Build O(1) keyword index from pattern strings
    if (m.contains("patterns")) {
      for (const auto &p : m["patterns"]) {
        // Extract core keyword (strip regex chars)
        std::string kw = p.get<std::string>();
        std::string clean;
        for (const char c : kw) {
          if (std::isalpha(static_cast<unsigned char>(c)) || c == ' ')
            clean +=
                static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
        }
        // Split on spaces, index each word >= 3 chars
        std::istringstream ss(clean);
        std::string word;
        while (ss >> word) {
          if (word.size() >= 3) {
            b.keyword_index[word] = idx;
          }
        }
      }
    }

    // Modifiers
    if (m.contains("modifiers")) {
      for (const auto &mod : m["modifiers"]) {
        Modifier md;
        md.pattern = std::regex(mod.value("pattern", ""),
                                std::regex_constants::icase |
                                    std::regex_constants::optimize);
        md.weight = mod.value("weight", 1.0);
        md.label = mod.value("label", "");
        mb.modifiers.push_back(std::move(md));
      }
    }

    // Mileage brackets
    if (m.contains("mileage_brackets")) {
      for (const auto &br : m["mileage_brackets"]) {
        MileageBracket mb_bracket;
        mb_bracket.max_km = br.value("max_km", 999999);
        mb_bracket.penalty = br.value("penalty", 0.0);
        mb.mileage_brackets.push_back(mb_bracket);
      }
      std::sort(mb.mileage_brackets.begin(), mb.mileage_brackets.end(),
                [](const MileageBracket &a, const MileageBracket &b) {
                  return a.max_km < b.max_km;
                });
    }

    b.models.push_back(std::move(mb));
  }

  return b;
}

// ─── Match & Score ──────────────────────────────────────────

static MatchResult match_and_score(std::string_view title, int mileage_km,
                                   const Baselines &baselines) {
  MatchResult result;
  const std::string norm = normalize(title);

  // Phase 1: O(1) keyword pre-filter
  std::vector<size_t> candidates;
  {
    std::istringstream ss(norm);
    std::string word;
    while (ss >> word) {
      if (const auto it = baselines.keyword_index.find(word);
          it != baselines.keyword_index.end()) {
        candidates.push_back(it->second);
      }
    }
  }

  // Deduplicate candidate indices
  std::sort(candidates.begin(), candidates.end());
  candidates.erase(std::unique(candidates.begin(), candidates.end()),
                   candidates.end());

  // Phase 2: Regex confirm only on candidates (not all models)
  for (const size_t idx : candidates) {
    if (idx >= baselines.models.size())
      continue;
    const auto &model = baselines.models[idx];

    for (const auto &pat : model.patterns) {
      if (std::regex_search(norm, pat)) {
        result.matched = true;
        result.model_name = model.name;
        result.adjusted_baseline = model.baseline;

        // Apply cumulative modifiers
        for (const auto &mod : model.modifiers) {
          if (std::regex_search(norm, mod.pattern)) {
            result.adjusted_baseline *= mod.weight;
            result.applied_modifiers.push_back(mod.label);
          }
        }

        // Apply mileage penalty via linear interpolation
        if (mileage_km > 0 && !model.mileage_brackets.empty()) {
          for (const auto &bracket : model.mileage_brackets) {
            if (mileage_km <= bracket.max_km) {
              result.mileage_penalty = bracket.penalty;
              result.adjusted_baseline *= (1.0 - bracket.penalty);
              break;
            }
          }
        }

        return result;
      }
    }
  }

  // Phase 3: Fallback full scan for models without keyword hits
  for (const auto &model : baselines.models) {
    for (const auto &pat : model.patterns) {
      if (std::regex_search(norm, pat)) {
        result.matched = true;
        result.model_name = model.name;
        result.adjusted_baseline = model.baseline;

        for (const auto &mod : model.modifiers) {
          if (std::regex_search(norm, mod.pattern)) {
            result.adjusted_baseline *= mod.weight;
            result.applied_modifiers.push_back(mod.label);
          }
        }

        if (mileage_km > 0 && !model.mileage_brackets.empty()) {
          for (const auto &bracket : model.mileage_brackets) {
            if (mileage_km <= bracket.max_km) {
              result.mileage_penalty = bracket.penalty;
              result.adjusted_baseline *= (1.0 - bracket.penalty);
              break;
            }
          }
        }

        return result;
      }
    }
  }

  return result;
}

// ─── Cloud Webhook Notification (n8n on Hugging Face) ───────

static bool notify_cloud_robot(const Deal &deal) {
  const char *webhook_url = std::getenv("N8N_WEBHOOK_URL");

  if (!webhook_url) {
    std::cerr << "[ERR] N8N_WEBHOOK_URL not set\n";
    return false;
  }

  // Parse host and path from URL
  std::string url_str(webhook_url);
  std::string host, path;
  size_t proto_end = url_str.find("://");
  if (proto_end != std::string::npos) {
    size_t host_start = proto_end + 3;
    size_t path_start = url_str.find("/", host_start);
    if (path_start != std::string::npos) {
      host = url_str.substr(host_start, path_start - host_start);
      path = url_str.substr(path_start);
    }
  }

  if (host.empty() || path.empty()) {
    std::cerr << "[ERR] Invalid N8N_WEBHOOK_URL structure\n";
    return false;
  }

  json payload;
  payload["title"] = deal.title;
  payload["price"] = deal.price;
  payload["market_value"] = deal.market_value;
  payload["savings_pct"] = deal.savings_pct;
  payload["model_year"] = deal.model_year;
  payload["mileage_km"] = deal.mileage_km;
  payload["url"] = deal.url;
  payload["phone"] = deal.phone;
  payload["source"] = deal.source;
  payload["modifiers"] = deal.modifiers;

  httplib::SSLClient cli(host, 443);
  cli.set_connection_timeout(std::chrono::seconds(5));
  cli.set_read_timeout(std::chrono::seconds(10));

  auto res = cli.Post(path, payload.dump(), "application/json");

  if (res && res->status == 200) {
    std::cout << "[CLOUD] Sent deal to n8n robot: " << deal.title << "\n";
    return true;
  } else {
    std::cerr << "[CLOUD] Failed to notify cloud (HTTP "
              << (res ? res->status : 0) << ")\n";
    return false;
  }
}

// ─── HTTP Server ────────────────────────────────────────────

int main() {
  const double threshold = [] {
    const char *env = std::getenv("DEAL_THRESHOLD_PCT");
    return env ? std::stod(env) : 0.85;
  }();

  const int min_year = [] {
    const char *env = std::getenv("MIN_YEAR");
    return env ? std::stoi(env) : 2015;
  }();

  std::cout << "═══════════════════════════════════════════\n"
            << "  Marketplace Sniper v5 — C++ Filter\n"
            << "  Threshold: " << static_cast<int>(threshold * 100) << "%\n"
            << "  Min Year:  " << min_year << "\n"
            << "  Telegram:  direct (no n8n)\n"
            << "═══════════════════════════════════════════\n";

  httplib::Server svr;

  // ── POST /filter ──────────────────────────────────────
  svr.Post("/filter", [&](const httplib::Request &req, httplib::Response &res) {
    const auto t0 = Clock::now();

    json listings;
    try {
      listings = json::parse(req.body);
    } catch (const json::exception &e) {
      res.status = 400;
      res.set_content("{\"error\":\"Invalid JSON: " + std::string(e.what()) +
                          "\"}",
                      "application/json");
      return;
    }

    if (!listings.is_array()) {
      res.status = 400;
      res.set_content("{\"error\":\"Expected JSON array\"}",
                      "application/json");
      return;
    }

    // Hot-reload baselines every request
    const Baselines baselines = load_baselines();

    json deals = json::array();
    int total = 0, matched = 0, flagged = 0;

    for (const auto &item : listings) {
      ++total;

      const std::string title = item.value("title", "");
      const double price = item.value("price", 0.0);
      const int year = item.value("model_year", 0);
      const int mileage = item.value("mileage_km", 0);
      const std::string url = item.value("url", "");
      const std::string phone = item.value("phone", "");
      const std::string source = item.value("source", "facebook");

      if (title.empty() || price <= 0)
        continue;
      if (year > 0 && year < min_year)
        continue;

      const auto result = match_and_score(title, mileage, baselines);
      if (!result.matched)
        continue;
      ++matched;

      const double savings_pct = 1.0 - (price / result.adjusted_baseline);
      if (savings_pct < (1.0 - threshold))
        continue;

      ++flagged;

      Deal deal;
      deal.title = title;
      deal.price = price;
      deal.market_value = result.adjusted_baseline;
      deal.savings_pct = savings_pct * 100.0;
      deal.model_year = year;
      deal.mileage_km = mileage;
      deal.url = url;
      deal.phone = phone;
      deal.source = source;
      deal.modifiers = result.applied_modifiers;

      // Fire Cloud Robot alert (n8n)
      notify_cloud_robot(deal);

      json d;
      d["title"] = title;
      d["price"] = price;
      d["market_value"] = result.adjusted_baseline;
      d["savings_pct"] = std::round(savings_pct * 1000.0) / 10.0;
      d["model_year"] = year;
      d["mileage_km"] = mileage;
      d["url"] = url;
      d["phone"] = phone;
      d["source"] = source;
      d["modifiers"] = result.applied_modifiers;
      d["mileage_penalty"] = result.mileage_penalty;
      deals.push_back(std::move(d));
    }

    const auto t1 = Clock::now();
    const auto us =
        std::chrono::duration_cast<std::chrono::microseconds>(t1 - t0).count();

    std::cout << "[FILTER] " << total << " listings → " << matched
              << " matched → " << flagged << " deals (" << us << "µs)\n";

    json response;
    response["deals"] = deals;
    response["stats"]["total"] = total;
    response["stats"]["matched"] = matched;
    response["stats"]["deals"] = flagged;
    response["stats"]["execution_us"] = us;

    res.set_content(response.dump(), "application/json");
  });

  // ── GET /health ───────────────────────────────────────
  svr.Get("/health", [](const httplib::Request &, httplib::Response &res) {
    res.set_content("{\"status\":\"ok\",\"service\":\"filter-v5\"}",
                    "application/json");
  });

  std::cout << "[FILTER] Listening on port " << PORT << "\n";
  svr.listen("0.0.0.0", PORT);

  return 0;
}
