"""AI layer for the review-analytics module.

Three capabilities, each with a deterministic heuristic fallback so the feature
degrades gracefully when no model/API key is available (offline demos, tests):

  generate_reviews  synthesise realistic customer reviews for a product
                    (the teacher's "让 AI 生成评价" — used to enrich sparse
                    products or bootstrap a demo)
  analyze_product   turn a product's reviews + stats into structured insight:
                    pros / cons / aspect themes / potential narrative / actions
  summarize_store   an executive summary over the whole store's potential

Every method returns a dict carrying ``generated_by: "ai" | "heuristic"`` so the
UI can badge AI-authored content.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional


def _extract_json(text: str) -> Optional[Any]:
    """Best-effort JSON extraction from an LLM response (handles ```json fences
    and leading/trailing prose)."""
    if not text:
        return None
    fenced = re.search(r"```(?:json)?\s*(.+?)```", text, re.DOTALL)
    candidate = fenced.group(1).strip() if fenced else text.strip()
    try:
        return json.loads(candidate)
    except Exception:
        pass
    # Fall back to the first balanced {...} or [...] block.
    for opener, closer in (("{", "}"), ("[", "]")):
        start = candidate.find(opener)
        end = candidate.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(candidate[start : end + 1])
            except Exception:
                continue
    return None


class AIReviewer:
    def __init__(self, model_client=None, logger=None):
        self.model = model_client
        self.logger = logger

    @property
    def available(self) -> bool:
        return self.model is not None and bool(getattr(self.model, "api_key", ""))

    def _log(self, msg: str) -> None:
        if self.logger is not None:
            self.logger.warning(msg)

    def _call(self, prompt: str, max_tokens: int = 900) -> str:
        return self.model.chat(
            user_id="analytics", message=prompt, use_history=False, max_tokens=max_tokens
        )

    # ------------------------------------------------------- generate reviews
    def generate_reviews(
        self, product: Dict[str, Any], n: int = 5, skew: str = "mixed"
    ) -> Dict[str, Any]:
        """Generate ``n`` review drafts for a product. ``skew`` in
        {"positive","mixed","critical"} nudges the sentiment mix."""
        n = max(1, min(int(n), 12))
        if self.available:
            try:
                prompt = self._reviews_prompt(product, n, skew)
                data = _extract_json(self._call(prompt, max_tokens=1200))
                items = data.get("reviews") if isinstance(data, dict) else data
                reviews = self._coerce_reviews(items, product)
                if reviews:
                    return {"generated_by": "ai", "reviews": reviews[:n]}
            except Exception as exc:
                self._log(f"generate_reviews LLM failed: {exc}")
        return {"generated_by": "heuristic", "reviews": self._heuristic_reviews(product, n, skew)}

    def _reviews_prompt(self, product: Dict[str, Any], n: int, skew: str) -> str:
        mix = {
            "positive": "以好评为主（4-5星），可有个别中评",
            "critical": "以中差评为主（1-3星），指出具体问题",
        }.get(skew, "真实的好中差评混合（1-5星，以4-5星居多）")
        return (
            "你是电商用户评价生成器。请为下面这款商品生成"
            f" {n} 条真实、口语化、中文的用户评价，{mix}。"
            "每条要提到具体的使用体验或商品属性，长度 15-40 字。\n"
            f"商品：{product.get('title','')}（{product.get('category','')} / {product.get('brand','')}）\n"
            f"简介：{product.get('description','')}\n\n"
            '只输出 JSON：{"reviews":[{"rating":5,"title":"很满意",'
            '"content":"...","aspects":["续航"]}]}'
        )

    def _coerce_reviews(self, items: Any, product: Dict[str, Any]) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        if not isinstance(items, list):
            return out
        for it in items:
            if not isinstance(it, dict):
                continue
            try:
                rating = max(1, min(5, int(it.get("rating", 5))))
            except Exception:
                rating = 5
            content = str(it.get("content", "")).strip()
            if not content:
                continue
            aspects = it.get("aspects") or []
            if not isinstance(aspects, list):
                aspects = []
            out.append({
                "rating": rating,
                "title": str(it.get("title", "")).strip()[:40],
                "content": content[:200],
                "aspects": [str(a)[:16] for a in aspects][:4],
            })
        return out

    def _heuristic_reviews(self, product: Dict[str, Any], n: int, skew: str) -> List[Dict[str, Any]]:
        from .review_store import (
            _ASPECTS_BY_CATEGORY, _DEFAULT_ASPECTS, _compose_comment,
            _sentiment_of, _title_for,
        )
        import random

        seed = abs(hash((product.get("product_id", ""), skew, n))) % (2 ** 32)
        rng = random.Random(seed)
        pool = _ASPECTS_BY_CATEGORY.get(product.get("category", ""), _DEFAULT_ASPECTS)
        brand = product.get("brand", "") or "该品牌"
        if skew == "positive":
            ratings = [5, 4, 5, 5, 4, 5, 4, 5, 5, 4, 5, 4]
        elif skew == "critical":
            ratings = [2, 3, 1, 3, 2, 4, 3, 2, 3, 1, 2, 3]
        else:
            ratings = [5, 4, 5, 3, 4, 5, 2, 4, 5, 3, 4, 5]
        reviews = []
        for i in range(n):
            rating = ratings[i % len(ratings)]
            sentiment = _sentiment_of(rating)
            aspect = rng.choice(pool)
            reviews.append({
                "rating": rating,
                "title": _title_for(sentiment, rng),
                "content": _compose_comment(product.get("category", ""), brand, aspect, sentiment, rng),
                "aspects": [aspect],
            })
        return reviews

    # -------------------------------------------------------- analyze product
    def analyze_product(
        self, product: Dict[str, Any], stats: Dict[str, Any],
        potential: Dict[str, Any], reviews: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        if self.available and reviews:
            try:
                prompt = self._analyze_prompt(product, stats, potential, reviews)
                data = _extract_json(self._call(prompt, max_tokens=1000))
                if isinstance(data, dict):
                    return self._coerce_insight(data, generated_by="ai")
            except Exception as exc:
                self._log(f"analyze_product LLM failed: {exc}")
        return self._heuristic_insight(product, stats, potential)

    def _analyze_prompt(self, product, stats, potential, reviews) -> str:
        sample = "\n".join(f"- {r['rating']}★ {r.get('content','')}" for r in reviews[:20])
        return (
            "你是电商商品分析师。基于以下评价数据，评估该商品的发展潜力并给出建议。\n"
            f"商品：{product.get('title','')}（{product.get('category','')}）\n"
            f"均分 {stats.get('avg_rating')}，共 {stats.get('count')} 条，"
            f"好评率 {int(stats.get('positive_share',0)*100)}%，"
            f"评分趋势 {stats.get('rating_trend')}，潜力分 {potential.get('score')}"
            f"（{potential.get('tier',{}).get('label','')}）。\n评价样本：\n{sample}\n\n"
            '只输出 JSON：{"summary":"一句话总结","pros":["优点"],"cons":["缺点"],'
            '"themes":[{"aspect":"续航","sentiment":"positive"}],'
            '"potential_narrative":"发展潜力分析","recommended_actions":["建议"],'
            '"risk_level":"low|medium|high"}'
        )

    def _coerce_insight(self, data: Dict[str, Any], generated_by: str) -> Dict[str, Any]:
        def _strlist(key, cap=6):
            v = data.get(key) or []
            if isinstance(v, str):
                v = [v]
            return [str(x).strip() for x in v if str(x).strip()][:cap] if isinstance(v, list) else []

        themes = []
        raw_themes = data.get("themes") or []
        if isinstance(raw_themes, list):
            for t in raw_themes[:8]:
                if isinstance(t, dict) and t.get("aspect"):
                    themes.append({
                        "aspect": str(t.get("aspect"))[:16],
                        "sentiment": t.get("sentiment", "neutral"),
                    })
        risk = data.get("risk_level", "medium")
        if risk not in ("low", "medium", "high"):
            risk = "medium"
        return {
            "generated_by": generated_by,
            "summary": str(data.get("summary", "")).strip()[:200],
            "pros": _strlist("pros"),
            "cons": _strlist("cons"),
            "themes": themes,
            "potential_narrative": str(data.get("potential_narrative", "")).strip()[:400],
            "recommended_actions": _strlist("recommended_actions"),
            "risk_level": risk,
        }

    def _heuristic_insight(self, product, stats, potential) -> Dict[str, Any]:
        tier = potential.get("tier", {})
        pos = int(stats.get("positive_share", 0) * 100)
        neg = int(stats.get("negative_share", 0) * 100)
        trend = stats.get("rating_trend", 0.0)
        top_aspects = stats.get("top_aspects", [])
        top_tags = stats.get("top_tags", [])
        aspect_words = "、".join(a["aspect"] for a in top_aspects[:3]) or "整体体验"

        trend_txt = ("近期评分持续走高，处于上升通道" if trend > 0.1
                     else "近期评分走弱，需警惕口碑下滑" if trend < -0.1
                     else "评分保持平稳")
        summary = (
            f"{product.get('title','该商品')}均分 {stats.get('avg_rating')}★，"
            f"好评率 {pos}%，潜力分 {potential.get('score')}（{tier.get('label','')}）；{trend_txt}。"
        )
        pros, cons = [], []
        if pos >= 60:
            pros.append(f"好评占比 {pos}%，用户满意度较高")
        if trend > 0.1:
            pros.append("评分趋势向上，增长势能强")
        if top_tags:
            pros.append("好评关键词：" + "、".join(t["tag"] for t in top_tags[:3]))
        if neg >= 20:
            cons.append(f"差评占比 {neg}%，存在集中问题待解决")
        if trend < -0.1:
            cons.append("近期评分下滑，需排查最新批次或服务")
        if stats.get("count", 0) < 5:
            cons.append("评价样本偏少，结论置信度有限")
        if not pros:
            pros.append("基础口碑稳定")
        if not cons:
            cons.append("暂无明显短板")

        actions_map = {
            "star": ["加大流量与备货投入", "沉淀好评做营销素材", "推出连带/升级款"],
            "rising": ["加投测试放大势能", "跟进近期好评点强化卖点", "适度增加库存"],
            "stable": ["优化主图与详情页转化", "用优惠券刺激复购", "持续监测评分变化"],
            "at_risk": ["排查差评根因（质量/物流/描述）", "联系近期差评用户挽回", "评估改款或下架"],
            "unrated": ["引导已购用户评价", "用 AI 生成示例评价预热"],
        }
        return {
            "generated_by": "heuristic",
            "summary": summary,
            "pros": pros[:5],
            "cons": cons[:5],
            "themes": [{"aspect": a["aspect"], "sentiment": "neutral"} for a in top_aspects[:5]],
            "potential_narrative": (
                f"综合满意度、增长势能与销售拉动，该商品当前潜力评级为「{tier.get('label','')}」。"
                f"{tier.get('advice','')}。核心关注点：{aspect_words}。"
            ),
            "recommended_actions": actions_map.get(tier.get("key", "stable"), actions_map["stable"]),
            "risk_level": "high" if tier.get("key") == "at_risk" else ("low" if tier.get("key") == "star" else "medium"),
        }

    # --------------------------------------------------------- summarize store
    def summarize_store(
        self, store_potential: Dict[str, Any], store_stats: Dict[str, Any]
    ) -> Dict[str, Any]:
        if self.available:
            try:
                prompt = self._store_prompt(store_potential, store_stats)
                data = _extract_json(self._call(prompt, max_tokens=900))
                if isinstance(data, dict):
                    return self._coerce_store_summary(data, generated_by="ai")
            except Exception as exc:
                self._log(f"summarize_store LLM failed: {exc}")
        return self._heuristic_store_summary(store_potential)

    def _store_prompt(self, sp, stats) -> str:
        tops = "、".join(p["title"] for p in sp.get("top_products", [])[:3])
        watch = "、".join(p["title"] for p in sp.get("watch_products", [])[:3])
        cats = "；".join(f"{c['category']} {c['avg_score']}" for c in sp.get("categories", [])[:5])
        return (
            "你是电商店铺运营顾问。基于以下店铺评价与潜力数据，写一份简短的经营洞察。\n"
            f"店铺潜力分 {sp.get('score')}（{sp.get('tier',{}).get('label','')}），"
            f"均分 {sp.get('avg_rating')}，评价 {sp.get('total_reviews')} 条，"
            f"好评率 {int(sp.get('positive_share',0)*100)}%，趋势 {sp.get('rating_trend')}。\n"
            f"明星/潜力商品：{tops}。需关注：{watch}。分类潜力：{cats}。\n\n"
            '只输出 JSON：{"headline":"一句话结论","highlights":["亮点"],'
            '"concerns":["风险"],"opportunities":["机会"],"strategic_actions":["行动建议"]}'
        )

    def _coerce_store_summary(self, data, generated_by) -> Dict[str, Any]:
        def _strlist(key, cap=6):
            v = data.get(key) or []
            if isinstance(v, str):
                v = [v]
            return [str(x).strip() for x in v if str(x).strip()][:cap] if isinstance(v, list) else []

        return {
            "generated_by": generated_by,
            "headline": str(data.get("headline", "")).strip()[:200],
            "highlights": _strlist("highlights"),
            "concerns": _strlist("concerns"),
            "opportunities": _strlist("opportunities"),
            "strategic_actions": _strlist("strategic_actions"),
        }

    def _heuristic_store_summary(self, sp) -> Dict[str, Any]:
        tier = sp.get("tier", {})
        tc = sp.get("tier_counts", {})
        pos = int(sp.get("positive_share", 0) * 100)
        trend = sp.get("rating_trend", 0.0)
        top = sp.get("top_products", [])
        watch = sp.get("watch_products", [])
        cats = sp.get("categories", [])

        headline = (
            f"店铺潜力评级「{tier.get('label','')}」（{sp.get('score')} 分），"
            f"均分 {sp.get('avg_rating')}★，好评率 {pos}%，"
            f"共 {tc.get('star',0)} 个明星款、{tc.get('rising',0)} 个潜力款、{tc.get('at_risk',0)} 个预警款。"
        )
        highlights = []
        if top:
            highlights.append("表现最佳：" + "、".join(p["title"] for p in top[:3]))
        if cats:
            highlights.append(f"最强分类：{cats[0]['category']}（潜力 {cats[0]['avg_score']}）")
        if trend > 0.05:
            highlights.append("店铺整体评分趋势向上")
        concerns = []
        if watch:
            concerns.append("需重点关注：" + "、".join(p["title"] for p in watch[:3]))
        if trend < -0.05:
            concerns.append("整体评分近期走弱，需排查共性问题")
        if cats:
            concerns.append(f"最弱分类：{cats[-1]['category']}（潜力 {cats[-1]['avg_score']}）")
        opportunities = [
            "将明星款的好评点复制到同类商品",
            "针对潜力款加投放大增长势能",
        ]
        actions = [
            "对预警款逐一排查差评根因并整改",
            "优化弱势分类的选品与详情页",
            "建立评分与潜力分的周度监控看板",
        ]
        return {
            "generated_by": "heuristic",
            "headline": headline,
            "highlights": highlights or ["店铺运行平稳"],
            "concerns": concerns or ["暂无显著风险"],
            "opportunities": opportunities,
            "strategic_actions": actions,
        }
