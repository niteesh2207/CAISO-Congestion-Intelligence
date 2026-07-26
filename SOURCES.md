# Sources and Data Provenance

Last reviewed: **July 25, 2026**.

The prototype prioritizes authoritative CAISO material for market, network-model, planning, outage, renewable, and storage context.

## Primary CAISO sources

1. **CAISO Network & Resource Modeling / Full Network Model**  
   https://www.caiso.com/market-operations/network-resource-modeling

2. **26M6 DB141 Full Network Model production deployment**  
   https://www.caiso.com/notices/26m6-db141-full-network-model-production-deployment

3. **2025–2026 Transmission Planning Process**  
   https://stakeholdercenter.caiso.com/RecurringStakeholderProcesses/2025-2026-Transmission-planning-process

4. **Board-approved 2025–2026 Transmission Plan**  
   https://www.caiso.com/documents/board-approved-2025-2026-transmission-plan.pdf

5. **2025–2026 Policy Assessment, Appendix F**  
   https://www.caiso.com/documents/appendix-f-policy-assessment-revised-draft-2025-2026-transmission-plan-may-2026.pdf

6. **CAISO OASIS**  
   https://oasis.caiso.com/mrioasis/logon.do

7. **OASIS Interface Specification**  
   https://www.caiso.com/documents/oasis-interfacespecification_v5_1_2clean_fall2017release.pdf

8. **CAISO Outages**  
   https://www.caiso.com/market-operations/outages

9. **Daily Renewable Reports**  
   https://www.caiso.com/library/daily-renewable-reports

10. **Daily Energy Storage Reports**  
    https://www.caiso.com/library/daily-energy-storage-reports

## Mapping stack

- Leaflet: https://leafletjs.com/
- OpenStreetMap: https://www.openstreetmap.org/
- CARTO basemaps: https://carto.com/basemaps/

## Important limitations

- The current prototype uses **illustrative regional placement** for several substations/elements unless a coordinate has been independently verified.
- Source/sink direction is an engineering/market interpretation and should ultimately be validated with applicable PTDF / market sensitivity data.
- Numerical LODF values are intentionally not fabricated. A production implementation should calculate outage sensitivities against the applicable solved network model and outage topology.
- The prototype does not claim that the displayed constraint is binding for a specific market interval unless supported by interval-specific OASIS data.
