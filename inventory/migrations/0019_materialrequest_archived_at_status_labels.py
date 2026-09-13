from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("inventory", "0018_alter_inventoryitem_bin_location_and_more")]

    operations = [
        migrations.AddField(
            model_name="materialrequest",
            name="archived_at",
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
        migrations.AlterField(
            model_name="pickticket",
            name="status",
            field=models.CharField(
                choices=[
                    ("OPEN", "Open"),
                    ("PICKED", "Picked"),
                    ("RECEIVED", "Ready for Delivery"),
                    ("CLOSED", "Closed/Delivered"),
                ],
                default="OPEN",
                max_length=20,
            ),
        ),
    ]
