---
slug: supermarket-replenishment
title: Supermarket Replenishment for Trims and Small Parts
doc_type: SOP
scope: org
acl: []
version: 1
---
# Supermarket Replenishment for Trims and Small Parts

> Synthetic demonstration document — not an official factory procedure.

## Purpose and Scope

A supermarket is a controlled buffer of trims and small parts staged near the point of use on the line, replenished on a pull signal from the line rather than pushed on a fixed schedule. This procedure covers supermarket replenishment for buttons (M10 Button 18L), zippers (M11 Metal Zipper 7in), labels (M12 Care Label, M13 Brand Label, M14 Hang Tag), and fasteners (M17 Elastic Tape 25mm, M18 Twill Tape, M19 Snap Fastener, M20 Drawcord) at both plants.

## Pull Signal

Each supermarket location carries a minimum and a target quantity per part per line. When a bin reaches its minimum quantity, the line's material handler raises a replenishment card to the store; the storekeeper must fulfil it from ACCEPTED stock within the same shift wherever the balance allows, since a stockout on a small part such as a snap fastener can stop an entire line as effectively as a fabric shortage.

## Sizing the Buffer

Minimum and target quantities are set from the style's bill of materials quantity per unit and the line's planned output rate for the shift, with enough margin to cover the replenishment lead time between the store and the line (typically under thirty minutes at both KTN and BYG). Buffers are reviewed whenever a line is loaded with a materially different style mix, since parts consumption per hour changes with the style.

## Reservation Interaction

Supermarket replenishment draws only from a material's available balance — accepted on-hand stock minus active reservations for other orders — and never reserves stock on the order's behalf beyond what the pull card requests. This keeps the supermarket buffer a physical staging mechanism, distinct from the reservation records used for planning and shortage calculations.

## Escalation

If the store cannot fulfil a pull card because the material's available balance is insufficient, the storekeeper escalates to the planner immediately rather than partially filling the bin, since a partial fill can mask a shortage that should instead trigger a reorder-point review or a due-date risk escalation.

## Related Procedures and Review

Buffer sizing for this procedure draws on the same bill-of-materials quantities used in Reorder Point Policy and Material Issue to Line, so a change to a style's trims consumption rate is reflected consistently across all three documents rather than updated in only one place. Supermarket locations are physically walked and recounted by the storekeeper at least weekly, independent of any pull card activity, to catch a miscount before it causes a line stoppage. New supermarket locations are not opened for a style until its buffer sizing has been reviewed and signed off by the planner.
