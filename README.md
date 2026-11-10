## ERPNext Shipping

A Shipping Integration for ERPNext with various platforms. Platforms integrated in this app are:

- [LetMeShip](https://www.letmeship.com/en/)
- [SendCloud](https://www.sendcloud.com/home-new/)
- [Starshipit](https://www.starshipit.com/) (Aramex, Australia Post, DHL, NZ Post and the other couriers on your Starshipit account)

> [!TIP]
> Please make sure to get your API access enabled first, by contacting the LetMeShip support.

## Features
- Creation of shipment to a carrier service (e.g. FedEx, UPS) via LetMeShip and SendCloud. 
- Compare shipping rates. 
- Printing the shipping label is also made available within the Shipment doctype.
- Templates for the parcel dimensions.
- Shipment tracking.

## Installation

Install [on Frappe Cloud](https://frappecloud.com/marketplace/apps/shipping) or your own server:

```bash
cd ~/frappe-bench
bench get-app https://github.com/frappe/erpnext-shipping.git --branch version-14
bench --site $MY_SITE install-app erpnext_shipping
```

## Setup

Some shipping providers require the contact details of the pickup contact. Please make sure that the **User** selected as the _Pickup Contact Person_ has a first name, last name, email address, and phone number before submitting the **Shipment**.

For the 'compare shipping rates' feature to work as expected, you need to generate an API key from your service provider. Service providers have their own specific doctypes similar to those from the `Integrations`. They can be enabled or disabled depending on your needs.

![LetMeShip 2020-08-05 09-54-28](https://user-images.githubusercontent.com/17470909/89377411-500c4f80-d724-11ea-8fe5-b11fec2a5c27.png)

### Fetch Shipping Rates
![core2](https://user-images.githubusercontent.com/17470909/89377460-70d4a500-d724-11ea-8550-a2813b936651.gif)

You can see the list of shipping rates by clicking the `Fetch Shipping Rates` button. Once you picked a rate, it will create the shipment for you. 

### Shipping Label
![71bcfc9d-9d66-4a58-8238-1eeab4e9a24f 2020-08-05 09-48-32](https://user-images.githubusercontent.com/17470909/89377478-78944980-d724-11ea-8120-a5374c6e4c5e.png)

The service provider will also provide the shipping label and to generate the label, click on the `Print Shipping Label` on top of the doctype.

-----------------------
#### License

MIT

## Starshipit

Settings: **Shipping > Carriers > Starshipit**. Enter the API key and subscription key from Starshipit (Settings > API) and use **Test Connection**.

On a submitted Shipment:

- **Fetch Shipping Rates** lists every service on the Starshipit account. Choosing one creates the order in Starshipit (free) and sets the courier and service.
- **Tools > Print Shipping Label** books the consignment and attaches the label PDF to the Shipment and its Delivery Notes. Booking is charged by the courier, so it is refused until **Allow Label Printing** is ticked; reprints are always allowed. Leave it off to print labels in Starshipit instead.
- **Starshipit > Manifest** manifests the printed order and attaches the manifest PDF.
- **Starshipit > Book Pickup** books a courier pickup for the pickup date and time window (couriers that support pickups only).
- **Starshipit > Commercial Invoice** attaches the commercial invoice for international orders, when the courier provides one.
- **Tools > Update Tracking** (and the daily job) reads the order status and courier tracking. For updates as they happen, paste the **Webhook URL** into Starshipit's webhook setting; set a **Webhook Secret** in both places to have every webhook signed (X-Starshipit-Signature).
- Cancelling the Shipment deletes the Starshipit order, or archives it once a label was printed.

When a draft Shipment has no parcels, they are added from its Delivery Notes. On each Item (Inventory tab) you can set the **Parcel Template** it ships in and tick **Ships in Own Box**:

- items that ship in their own box get one parcel per unit, in their own Parcel Template;
- all other items share one parcel: the Shipment's Parcel Template, else the largest Parcel Template of those items, else the Default Parcel Template in Starshipit settings.

Parcel weight is the items' weight (per stock unit, times stock quantity) plus the box weight. Parcels can always be edited by hand before fetching rates.

