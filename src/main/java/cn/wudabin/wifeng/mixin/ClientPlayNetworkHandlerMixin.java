package cn.wudabin.wifeng.mixin;

import cn.wudabin.wifeng.WifeNgClient;
import net.minecraft.client.network.ClientPlayNetworkHandler;
import net.minecraft.network.packet.s2c.play.GameMessageS2CPacket;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

@Mixin(ClientPlayNetworkHandler.class)
public abstract class ClientPlayNetworkHandlerMixin {
    @Inject(method = "onGameMessage", at = @At("HEAD"))
    private void wifeNgCaptureChat(GameMessageS2CPacket packet, CallbackInfo info) {
        WifeNgClient.CHAT_EVENTS.record(packet);
    }
}
