"""E-commerce tool implementations backed by the virtual store (CatalogStore)."""

from __future__ import annotations

from typing import Any, Dict

from .registry import ToolRegistry, ToolSpec
from .catalog_store import CatalogStore
from .schemas import (
    ProductSearchInput,
    ProductSearchOutput,
    GetProductInput,
    GetProductOutput,
    CheckInventoryInput,
    CheckInventoryOutput,
    GetOrderInput,
    GetOrderOutput,
    ListOrdersInput,
    ListOrdersOutput,
    TrackShipmentInput,
    TrackShipmentOutput,
    RecommendInput,
    RecommendOutput,
    ListCouponsInput,
    ListCouponsOutput,
    ApplyCouponInput,
    ApplyCouponOutput,
    InitiateReturnInput,
    InitiateReturnOutput,
)


def register_commerce_tools(registry: ToolRegistry, catalog: CatalogStore) -> None:
    """Register virtual-store tools so the agent can answer shopping questions."""

    def product_search_tool(args: ProductSearchInput) -> Dict[str, Any]:
        hits = catalog.search_products(
            query=args.query,
            category=args.category,
            max_price=args.max_price,
            top_k=args.top_k,
        )
        return {"query": args.query, "hits": hits}

    def get_product_tool(args: GetProductInput) -> Dict[str, Any]:
        product = catalog.get_product(args.product_id)
        return {"found": product is not None, "product": product}

    def check_inventory_tool(args: CheckInventoryInput) -> Dict[str, Any]:
        variants = catalog.check_inventory(
            product_id=args.product_id, sku_code=args.sku_code
        )
        return {"variants": variants}

    def get_order_tool(args: GetOrderInput) -> Dict[str, Any]:
        order = catalog.get_order(args.order_id)
        return {"found": order is not None, "order": order}

    def list_orders_tool(args: ListOrdersInput) -> Dict[str, Any]:
        orders = catalog.list_orders(customer_id=args.customer_id, limit=args.limit)
        return {"customer_id": args.customer_id, "orders": orders}

    def track_shipment_tool(args: TrackShipmentInput) -> Dict[str, Any]:
        shipment = None
        if args.tracking_no:
            shipment = catalog.get_shipment_by_tracking(args.tracking_no)
        elif args.order_id:
            shipment = catalog.get_shipment_by_order(args.order_id)
        return {"found": shipment is not None, "shipment": shipment}

    def recommend_tool(args: RecommendInput) -> Dict[str, Any]:
        recs = catalog.recommend(query=args.query, top_k=args.top_k)
        return {"recommendations": recs}

    def list_coupons_tool(args: ListCouponsInput) -> Dict[str, Any]:
        coupons = catalog.list_coupons(min_spend=args.min_spend)
        return {"coupons": coupons}

    def apply_coupon_tool(args: ApplyCouponInput) -> Dict[str, Any]:
        return catalog.evaluate_coupon(code=args.code, order_total=args.order_total)

    def initiate_return_tool(args: InitiateReturnInput) -> Dict[str, Any]:
        return catalog.create_return(
            order_id=args.order_id,
            customer_id=args.customer_id,
            reason=args.reason,
            sku_code=args.sku_code,
        )

    specs = [
        ToolSpec(
            name="product_search",
            description="Search the shop catalog by keyword, optional category and max price.",
            input_model=ProductSearchInput,
            output_model=ProductSearchOutput,
            handler=product_search_tool,
        ),
        ToolSpec(
            name="get_product_detail",
            description="Get full product detail including variants (SKU), price and stock.",
            input_model=GetProductInput,
            output_model=GetProductOutput,
            handler=get_product_tool,
        ),
        ToolSpec(
            name="check_inventory",
            description="Check stock for a product or a specific SKU code.",
            input_model=CheckInventoryInput,
            output_model=CheckInventoryOutput,
            handler=check_inventory_tool,
        ),
        ToolSpec(
            name="get_order",
            description="Get an order's status, items and shipment by order id.",
            input_model=GetOrderInput,
            output_model=GetOrderOutput,
            handler=get_order_tool,
        ),
        ToolSpec(
            name="list_orders",
            description="List a customer's recent orders.",
            input_model=ListOrdersInput,
            output_model=ListOrdersOutput,
            handler=list_orders_tool,
        ),
        ToolSpec(
            name="track_shipment",
            description="Track logistics by order id or tracking number.",
            input_model=TrackShipmentInput,
            output_model=TrackShipmentOutput,
            handler=track_shipment_tool,
        ),
        ToolSpec(
            name="recommend_products",
            description="Recommend products for a customer, optionally guided by a query/preference.",
            input_model=RecommendInput,
            output_model=RecommendOutput,
            handler=recommend_tool,
        ),
        ToolSpec(
            name="list_coupons",
            description="List currently active coupons, optionally filtered by spend amount.",
            input_model=ListCouponsInput,
            output_model=ListCouponsOutput,
            handler=list_coupons_tool,
        ),
        ToolSpec(
            name="apply_coupon",
            description="Validate a coupon code against an order total and compute the discount.",
            input_model=ApplyCouponInput,
            output_model=ApplyCouponOutput,
            handler=apply_coupon_tool,
        ),
        ToolSpec(
            name="initiate_return",
            description="Open an after-sales return/refund request for an order or SKU.",
            input_model=InitiateReturnInput,
            output_model=InitiateReturnOutput,
            handler=initiate_return_tool,
        ),
    ]
    for spec in specs:
        registry.register(spec)
