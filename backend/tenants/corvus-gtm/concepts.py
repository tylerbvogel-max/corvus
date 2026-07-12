"""Concept neuron definitions for the go-to-market knowledge domain."""

CONCEPT_DEFINITIONS: list[dict] = [
    {
        "label": "Positioning",
        "summary": "Deliberately setting the context in which a buyer evaluates a product: competitive alternatives, unique attributes, value, who cares most, and category",
        "content": (
            "Positioning is not messaging — it is the choice of context that makes a "
            "product's strengths obvious (Dunford's five components: competitive "
            "alternatives, unique attributes, value and proof, target segment, market "
            "category). A technically superior product positioned in the wrong category "
            "loses to a weaker product whose context makes it legible. Repositioning is "
            "often higher-leverage than rebuilding."
        ),
        "direct_patterns": ["%position%", "%categor%"],
        "content_patterns": ["%position%", "%differentiat%", "%alternativ%", "%segment%", "%category%"],
    },
    {
        "label": "Product-Market Fit",
        "summary": "The empirical state where a specific market pulls the product out of you — measured by retention and organic demand, not by features shipped",
        "content": (
            "PMF is an observed market behavior, not a milestone declared by the builder: "
            "retention curves flattening above zero, organic word-of-mouth, users angry "
            "when the product breaks. Pre-PMF, the correct motions are unscalable — "
            "founder-led sales, hand-recruiting users, doing things that don't scale — "
            "because their purpose is learning, not efficiency."
        ),
        "direct_patterns": ["%product-market%", "%PMF%"],
        "content_patterns": ["%product-market fit%", "%retention%", "%word-of-mouth%", "%pull%"],
    },
    {
        "label": "Distribution Advantage",
        "summary": "How a product reaches buyers is a design decision as important as the product itself; the best product with no channel loses to a worse product with one",
        "content": (
            "Every successful product pairs with a distribution motion matched to its "
            "price point: self-serve/PLG for low ACV, inside sales for mid, field sales "
            "for high. Builders systematically underweight distribution because building "
            "is legible to them and selling is not. First-ten-customers work is manual, "
            "founder-led, and unscalable by design."
        ),
        "direct_patterns": ["%distribut%", "%channel%"],
        "content_patterns": ["%distribution%", "%channel%", "%sales motion%", "%self-serve%", "%PLG%", "%first customers%"],
    },
    {
        "label": "Willingness to Pay",
        "summary": "Price is discovered from buyer value and alternatives, not derived from cost; the pricing metric (per-seat, per-usage) matters more than the number",
        "content": (
            "Pricing anchors to the buyer's next-best alternative and the value metric "
            "that scales with their success, never to the builder's costs. Packaging "
            "(what goes in which tier) is usually higher-leverage than the price point. "
            "Underpricing by technical founders is chronic: price communicates category "
            "and quality, and mid-market accessibility is a packaging problem, not just "
            "a discount problem."
        ),
        "direct_patterns": ["%pricing%", "%willingness%"],
        "content_patterns": ["%pricing%", "%willingness to pay%", "%value metric%", "%packaging%", "%tier%"],
    },
    {
        "label": "BATNA",
        "summary": "Negotiating power comes from the best alternative to a negotiated agreement — improve the alternative, not the argument",
        "content": (
            "The strongest move in any negotiation happens outside the room: developing "
            "a credible alternative (competing offer, walk-away option) changes the "
            "counterparty's math more than any argument. Anchoring first with a "
            "defensible number, negotiating interests rather than positions, and "
            "never accepting a first offer in a market where negotiation is expected "
            "are doctrine; the applied skill is calibrating them to the specific market."
        ),
        "direct_patterns": ["%BATNA%", "%negotiat%"],
        "content_patterns": ["%BATNA%", "%negotiat%", "%anchor%", "%offer%", "%walk away%"],
    },
]
